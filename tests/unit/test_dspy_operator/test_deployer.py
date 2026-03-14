"""Tests for app_operator_dspy.agents.deployer."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import dspy

from app_operator_dspy.agents.deployer import DeploymentAgent, _FixHistory, strip_code_fences
from app_operator_dspy.tools.shell import ShellResult


def _shell_ok(output: str = "Exit code: 0\nStdout:\nOK") -> ShellResult:
    """Create ShellResult for successful command."""
    return ShellResult(0, output)


def _shell_fail(output: str) -> ShellResult:
    """Create ShellResult for failed command (extract exit code from output)."""
    code = 1
    if output.startswith("Exit code: "):
        try:
            code = int(output.split("\n")[0].split(": ")[1])
        except (ValueError, IndexError):
            pass
    return ShellResult(code, output)


def _mock_agent(deploy_script="#!/bin/bash\nexit 0", health_script="#!/bin/bash\nexit 0"):
    agent = DeploymentAgent()
    agent.gen_deploy = MagicMock(return_value=dspy.Prediction(deploy_script=deploy_script))
    agent.gen_health = MagicMock(return_value=dspy.Prediction(health_check_script=health_script))
    agent.repair_agent = MagicMock(
        return_value=dspy.Prediction(fix_summary="fixed port conflict"),
    )
    agent._fix_history.consolidate = MagicMock(
        return_value=dspy.Prediction(
            consolidated_summary="## Failure Pattern: port conflict\n* Attempt 1: fixed",
        )
    )
    return agent


class TestDeploymentAgent:
    def test_is_dspy_module(self):
        agent = DeploymentAgent()
        assert isinstance(agent, dspy.Module)

    def test_gen_deploy_gen_health_repair_agent_are_react_with_tools(self):
        """gen_deploy, gen_health, repair_agent are ReAct instances with DEPLOYER_TOOLS."""
        agent = DeploymentAgent()
        assert isinstance(agent.gen_deploy, dspy.ReAct)
        assert isinstance(agent.gen_health, dspy.ReAct)
        assert isinstance(agent.repair_agent, dspy.ReAct)
        tool_names = set(agent.gen_deploy.tools.keys())
        expected = {"run_shell", "read_file", "write_file", "run_health_check", "list_files", "finish"}
        assert expected <= tool_names
        assert agent.gen_deploy.tools.keys() == agent.gen_health.tools.keys()
        assert agent.gen_health.tools.keys() == agent.repair_agent.tools.keys()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_successful_first_attempt(self, mock_shell, tmp_path):
        mock_shell.return_value = _shell_ok()
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues")

        assert result.success is True
        assert result.attempts == 1
        assert agent.gen_deploy.call_count == 1
        assert agent.repair_agent.call_count == 0

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_deploy_fails_then_succeeds(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nport in use"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),  # deploy success
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),  # health success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 2
        assert agent.repair_agent.call_count == 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_health_fail_triggers_full_retry(self, mock_shell, tmp_path):
        """Health fail triggers fix + cleanup, then full retry (re-deploy) on next iteration."""
        mock_shell.side_effect = [
            _shell_ok(),  # deploy success
            _shell_fail("Exit code: 1\nStderr:\nbad endpoint"),  # health fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),  # deploy success (retry)
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),  # health success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 2
        assert agent.repair_agent.call_count == 1
        # Verify cleanup was called after health failure
        cleanup_call = mock_shell.call_args_list[2]
        assert "cleanup" in cleanup_call.args[0]

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_max_attempts_exceeded(self, mock_shell, tmp_path):
        mock_shell.return_value = _shell_fail("Exit code: 1\nStderr:\nfailed")
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=2)

        assert result.success is False
        assert result.attempts == 2
        assert "failed" in result.error

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_scripts_written_to_sds_dir(self, mock_shell, tmp_path):
        mock_shell.return_value = _shell_ok()
        agent = _mock_agent(deploy_script="#!/bin/bash\ndeploy", health_script="#!/bin/bash\ncheck")

        agent.forward(str(tmp_path), "analysis", "issues")

        assert (tmp_path / ".sds" / "deploy.sh").read_text() == "#!/bin/bash\ndeploy"
        assert (tmp_path / ".sds" / "health_check.sh").read_text() == "#!/bin/bash\ncheck"

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_scripts_generated_once_not_regenerated(self, mock_shell, tmp_path):
        """Scripts are generated once upfront, then fixed — never regenerated."""
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy fail again
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=2)

        assert agent.gen_deploy.call_count == 1
        assert agent.gen_health.call_count == 1
        assert agent.repair_agent.call_count >= 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_first_fix_skips_consolidation(self, mock_shell, tmp_path):
        """First fix has no prior history — consolidation is skipped."""
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        agent._fix_history.consolidate.assert_not_called()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_consolidation_called_on_second_fix(self, mock_shell, tmp_path):
        """Consolidation runs when there's prior fix history."""
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_fail("Exit code: 1\nStderr:\nfail again"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),  # deploy success
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),  # health success
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        agent._fix_history.consolidate.assert_called_once()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_compose_override_written_on_fix(self, mock_shell, tmp_path):
        """Repair agent edits files via tools; simulate override write via mock side_effect."""
        agent = _mock_agent()
        override_content = "services:\n  backend:\n    ports:\n      - '5001:5000'"

        def write_override_and_return(**kwargs):
            repo_path = kwargs.get("repo_path", "")
            if repo_path:
                (Path(repo_path) / "docker-compose.override.yml").write_text(override_content)
            return dspy.Prediction(fix_summary="remapped port 5000 to 5001")

        agent.repair_agent = MagicMock(side_effect=write_override_and_return)
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nport in use"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),  # deploy success
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),  # health success
        ]

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert (tmp_path / "docker-compose.override.yml").exists()
        assert "5001:5000" in (tmp_path / "docker-compose.override.yml").read_text()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_cleanup_called_between_retries(self, mock_shell, tmp_path):
        """Cleanup runs between failed deploy and retry."""
        mock_shell.side_effect = [
            _shell_fail("Exit code: 1\nStderr:\nfail"),  # deploy fail
            _shell_ok("Exit code: 0\nStdout:\ncleanup done"),  # cleanup
            _shell_ok(),  # deploy success
            _shell_ok("Exit code: 0\nStdout:\nhealthy"),  # health success
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        # Verify cleanup was called (2nd shell call)
        cleanup_call = mock_shell.call_args_list[1]
        assert "cleanup" in cleanup_call.args[0]


class TestFixHistory:
    """Tests for _FixHistory (fix history consolidation, migrated from summarizer)."""

    def test_has_consolidate_module(self):
        """_FixHistory wraps a dspy ChainOfThought for consolidation."""
        fh = _FixHistory()
        assert isinstance(fh.consolidate, dspy.ChainOfThought)

    def test_first_fix_returns_summary_without_consolidation(self):
        """First fix has no prior history — consolidation is skipped."""
        fh = _FixHistory()
        fh.consolidate = MagicMock()

        fh.append(1, "fixed port conflict")

        assert fh.history == "Attempt 1: fixed port conflict"
        fh.consolidate.assert_not_called()

    def test_second_fix_calls_consolidation(self):
        """Second fix has prior history — consolidation is called."""
        fh = _FixHistory()
        fh.consolidate = MagicMock(
            return_value=dspy.Prediction(
                consolidated_summary="## Failure Pattern: port conflict\n* Attempt 1: fixed\n* Attempt 2: retried"
            )
        )
        fh.append(1, "fixed port conflict")

        fh.append(2, "retried with different port")

        assert "Attempt 2" in fh.history
        fh.consolidate.assert_called_once()
        assert fh.history == "## Failure Pattern: port conflict\n* Attempt 1: fixed\n* Attempt 2: retried"

    def test_reset_clears_history(self):
        """reset() clears history for next deployment run."""
        fh = _FixHistory()
        fh.append(1, "fixed")

        fh.reset()

        assert fh.history == ""


class TestStripCodeFences:
    def test_strips_bash_fences(self):
        text = "```bash\n#!/bin/bash\necho hello\n```"
        assert strip_code_fences(text) == "#!/bin/bash\necho hello"

    def test_strips_generic_fences(self):
        text = "```\nsome code\n```"
        assert strip_code_fences(text) == "some code"

    def test_no_fences_passthrough(self):
        text = "#!/bin/bash\necho hello"
        assert strip_code_fences(text) == "#!/bin/bash\necho hello"

    def test_strips_surrounding_whitespace(self):
        text = "  ```sh\n#!/bin/bash\nexit 0\n```  "
        assert strip_code_fences(text) == "#!/bin/bash\nexit 0"
