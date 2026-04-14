"""Tests for app_operator_dspy.agents.rlm_deployer."""

from unittest.mock import MagicMock, patch

import dspy

from app_operator_dspy.agents.rlm_deployer import (
    RLMDeploymentAgent,
    _blocked_run_shell,
)
from app_operator_dspy.tools.shell import ShellResult


def _shell_ok(output: str = "Exit code: 0\nStdout:\nOK") -> ShellResult:
    return ShellResult(0, output)


def _shell_fail(output: str) -> ShellResult:
    code = 1
    if output.startswith("Exit code: "):
        try:
            code = int(output.split("\n")[0].split(": ")[1])
        except (ValueError, IndexError):
            pass
    return ShellResult(code, output)


def _healthy_verdict():
    return dspy.Prediction(
        healthy=True,
        assessment="all healthy",
        diagnosis="",
        script_was_fixed=False,
    )


def _unhealthy_verdict(diagnosis: str = "service down"):
    return dspy.Prediction(
        healthy=False,
        assessment="health check failed",
        diagnosis=diagnosis,
        script_was_fixed=False,
    )


def _mock_rlm_agent():
    agent = RLMDeploymentAgent.__new__(RLMDeploymentAgent)
    dspy.Module.__init__(agent)
    agent.gen_deploy = MagicMock(
        return_value=dspy.Prediction(deploy_script="#!/bin/bash\nexit 0"),
    )
    agent.gen_health = MagicMock(
        return_value=dspy.Prediction(health_check_script="#!/bin/bash\nexit 0"),
    )
    agent.repair_agent = MagicMock(
        return_value=dspy.Prediction(fix_summary="fixed port conflict"),
    )
    agent.health_judge = MagicMock(return_value=_healthy_verdict())
    from app_operator_dspy.agents.rlm_deployer import _FixHistory

    agent._fix_history = _FixHistory()
    agent._fix_history.consolidate = MagicMock(
        return_value=dspy.Prediction(
            consolidated_summary="## Failure Pattern\n* Attempt 1: fixed",
        ),
    )
    return agent


class TestBlockedRunShell:
    def test_returns_error_message(self):
        result = _blocked_run_shell("echo hi")
        assert "ERROR" in result
        assert "not available" in result

    def test_has_run_shell_name(self):
        assert _blocked_run_shell.__name__ == "run_shell"

    def test_accepts_kwargs(self):
        result = _blocked_run_shell(command="ls", timeout=10)
        assert "not available" in result


class TestRepairErrorContext:
    """Verify the structured error_context format passed to the repair RLM."""

    @patch("app_operator_dspy.agents.rlm_deployer.run_shell")
    def test_error_context_has_structured_delimiters(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nport in use"),
            _shell_ok(),  # cleanup
            _shell_ok(),  # deploy success
        ]
        agent = _mock_rlm_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        ctx = agent.repair_agent.call_args.kwargs["error_context"]
        assert "repo_path:" in ctx
        assert "deploy_path:" in ctx
        assert "health_path:" in ctx
        assert "attempt:" in ctx
        assert "max_attempts:" in ctx
        assert "---ERROR_OUTPUT_START---" in ctx
        assert "---ERROR_OUTPUT_END---" in ctx
        assert "---FIX_HISTORY_START---" in ctx
        assert "---FIX_HISTORY_END---" in ctx
        assert "do NOT have access to run_shell" in ctx


class TestPostProcessScripts:
    def test_lowercases_project_name(self, tmp_path):
        script = '#!/bin/bash\nPROJECT_NAME=$(basename "$APP_DIR")\n'
        path = tmp_path / "deploy.sh"
        path.write_text(script)

        RLMDeploymentAgent._post_process_scripts(str(path), str(tmp_path / "missing.sh"))

        result = path.read_text()
        assert "tr '[:upper:]' '[:lower:]'" in result

    def test_replaces_hyphenated_docker_compose(self, tmp_path):
        script = "#!/bin/bash\ndocker-compose up -d\ndocker-compose ps\n"
        path = tmp_path / "deploy.sh"
        path.write_text(script)

        RLMDeploymentAgent._post_process_scripts(str(path), str(tmp_path / "missing.sh"))

        result = path.read_text()
        assert "docker-compose" not in result
        assert "docker compose" in result

    def test_adds_project_name_flag(self, tmp_path):
        script = "#!/bin/bash\ndocker compose up -d\n"
        path = tmp_path / "deploy.sh"
        path.write_text(script)

        RLMDeploymentAgent._post_process_scripts(str(path), str(tmp_path / "missing.sh"))

        result = path.read_text()
        assert '--project-name "$PROJECT_NAME"' in result

    def test_does_not_duplicate_project_name_flag(self, tmp_path):
        script = '#!/bin/bash\ndocker compose --project-name "$PROJECT_NAME" up -d\n'
        path = tmp_path / "deploy.sh"
        path.write_text(script)

        RLMDeploymentAgent._post_process_scripts(str(path), str(tmp_path / "missing.sh"))

        result = path.read_text()
        assert result.count("--project-name") == 1

    def test_skips_missing_files(self, tmp_path):
        # Should not raise when files don't exist
        RLMDeploymentAgent._post_process_scripts(
            str(tmp_path / "missing1.sh"),
            str(tmp_path / "missing2.sh"),
        )

    def test_processes_both_scripts(self, tmp_path):
        deploy = tmp_path / "deploy.sh"
        health = tmp_path / "health.sh"
        deploy.write_text("docker-compose up\n")
        health.write_text("docker-compose ps\n")

        RLMDeploymentAgent._post_process_scripts(str(deploy), str(health))

        assert "docker compose" in deploy.read_text()
        assert "docker compose" in health.read_text()


class TestRepairFailureHandling:
    """Fix 5: repair RLM failures don't count as deploy attempts."""

    @patch("app_operator_dspy.agents.rlm_deployer.run_shell")
    def test_repair_exception_does_not_count_as_attempt(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy 1
            _shell_ok(),  # cleanup
            # repair raises exception, attempt not counted
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy 1 retry
            _shell_ok(),  # cleanup
            _shell_ok(),  # deploy 2 success
        ]
        agent = _mock_rlm_agent()
        agent.repair_agent = MagicMock(
            side_effect=[
                RuntimeError("rate limit"),
                dspy.Prediction(fix_summary="fixed"),
            ],
        )

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True

    @patch("app_operator_dspy.agents.rlm_deployer.run_shell")
    def test_max_repair_failures_causes_failure(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),
            _shell_ok(),  # cleanup
            _shell_fail("Exit code: 1\nStderr:\nfail"),
            _shell_ok(),  # cleanup
            _shell_fail("Exit code: 1\nStderr:\nfail"),
            _shell_ok(),  # cleanup
        ]
        agent = _mock_rlm_agent()
        agent.repair_agent = MagicMock(side_effect=RuntimeError("rate limit"))

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=5)

        assert result.success is False
        assert "repair agent failed 3 times" in result.error


class TestPostProcessCalledInPipeline:
    """Verify _post_process_scripts is called after generation and after repair."""

    @patch("app_operator_dspy.agents.rlm_deployer.run_shell")
    def test_post_process_called_after_generation(self, mock_shell, tmp_path):
        mock_shell.return_value = _shell_ok()
        agent = _mock_rlm_agent()

        with patch.object(RLMDeploymentAgent, "_post_process_scripts") as mock_pp:
            agent.forward(str(tmp_path), "analysis", "issues")
            assert mock_pp.call_count >= 1

    @patch("app_operator_dspy.agents.rlm_deployer.run_shell")
    def test_post_process_called_after_repair(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),
            _shell_ok(),  # cleanup
            _shell_ok(),  # deploy success
        ]
        agent = _mock_rlm_agent()

        with patch.object(RLMDeploymentAgent, "_post_process_scripts") as mock_pp:
            agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)
            # Called once after generation, once after repair
            assert mock_pp.call_count == 2
