"""Tests for app_operator_dspy.agents.deployer."""

from unittest.mock import MagicMock, patch

import dspy

from app_operator_dspy.agents.deployer import DeploymentAgent, strip_code_fences


def _mock_agent(deploy_script="#!/bin/bash\nexit 0", health_script="#!/bin/bash\nexit 0"):
    agent = DeploymentAgent()
    agent.gen_deploy = MagicMock(return_value=dspy.Prediction(deploy_script=deploy_script))
    agent.gen_health = MagicMock(return_value=dspy.Prediction(health_check_script=health_script))
    agent.fix_error = MagicMock(
        return_value=dspy.Prediction(
            fixed_deploy_script=deploy_script,
            fixed_health_check_script=health_script,
            compose_override="",
            fix_summary="fixed port conflict",
        )
    )
    agent.consolidate = MagicMock(
        return_value=dspy.Prediction(
            consolidated_summary="## Failure Pattern: port conflict\n* Attempt 1: fixed",
        )
    )
    return agent


class TestDeploymentAgent:
    def test_is_dspy_module(self):
        agent = DeploymentAgent()
        assert isinstance(agent, dspy.Module)

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_successful_first_attempt(self, mock_shell, tmp_path):
        mock_shell.return_value = "Exit code: 0\nStdout:\nOK"
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues")

        assert result.success is True
        assert result.attempts == 1
        assert agent.gen_deploy.call_count == 1
        assert agent.fix_error.call_count == 0

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_deploy_fails_then_succeeds(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nport in use",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 2
        assert agent.fix_error.call_count == 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_health_fix_recheck_succeeds(self, mock_shell, tmp_path):
        """Post-fix recheck: health fails, fix script, re-run health WITHOUT re-deploying."""
        mock_shell.side_effect = [
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 1\nStderr:\nwrong endpoint",  # health fail
            "Exit code: 0\nStdout:\nhealthy",  # health recheck success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 1  # No re-deploy needed
        assert agent.fix_error.call_count == 1
        # Scripts generated once, not regenerated
        assert agent.gen_deploy.call_count == 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_health_fix_recheck_fails_triggers_full_retry(self, mock_shell, tmp_path):
        """Post-fix recheck fails, so a full retry (re-deploy) happens."""
        mock_shell.side_effect = [
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 1\nStderr:\nbad endpoint",  # health fail
            "Exit code: 1\nStderr:\nstill bad",  # health recheck fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",  # deploy success (retry)
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 2

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_max_attempts_exceeded(self, mock_shell, tmp_path):
        mock_shell.return_value = "Exit code: 1\nStderr:\nfailed"
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=2)

        assert result.success is False
        assert result.attempts == 2
        assert "failed" in result.error

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_scripts_written_to_sds_dir(self, mock_shell, tmp_path):
        mock_shell.return_value = "Exit code: 0\nStdout:\nOK"
        agent = _mock_agent(deploy_script="#!/bin/bash\ndeploy", health_script="#!/bin/bash\ncheck")

        agent.forward(str(tmp_path), "analysis", "issues")

        assert (tmp_path / ".sds" / "deploy.sh").read_text() == "#!/bin/bash\ndeploy"
        assert (tmp_path / ".sds" / "health_check.sh").read_text() == "#!/bin/bash\ncheck"

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_scripts_generated_once_not_regenerated(self, mock_shell, tmp_path):
        """Scripts are generated once upfront, then fixed — never regenerated."""
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nfail",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 1\nStderr:\nfail",  # deploy fail again
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=2)

        assert agent.gen_deploy.call_count == 1
        assert agent.gen_health.call_count == 1
        assert agent.fix_error.call_count >= 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_first_fix_skips_consolidation(self, mock_shell, tmp_path):
        """First fix has no prior history — consolidation is skipped."""
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nfail",
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",
            "Exit code: 0\nStdout:\nhealthy",
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        agent.consolidate.assert_not_called()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_consolidation_called_on_second_fix(self, mock_shell, tmp_path):
        """Consolidation runs when there's prior fix history."""
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nfail",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 1\nStderr:\nfail again",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        agent.consolidate.assert_called_once()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_compose_override_written_on_fix(self, mock_shell, tmp_path):
        """Fix agent can output compose override for port/image fixes."""
        override_content = "services:\n  backend:\n    ports:\n      - '5001:5000'"
        agent = _mock_agent()
        agent.fix_error = MagicMock(
            return_value=dspy.Prediction(
                fixed_deploy_script="#!/bin/bash\nexit 0",
                fixed_health_check_script="#!/bin/bash\nexit 0",
                compose_override=override_content,
                fix_summary="remapped port 5000 to 5001",
            )
        )
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nport in use",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert (tmp_path / "docker-compose.override.yml").exists()
        assert "5001:5000" in (tmp_path / "docker-compose.override.yml").read_text()

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_cleanup_called_between_retries(self, mock_shell, tmp_path):
        """Cleanup runs between failed deploy and retry."""
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nfail",  # deploy fail
            "Exit code: 0\nStdout:\ncleanup done",  # cleanup
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]
        agent = _mock_agent()

        agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        # Verify cleanup was called (2nd shell call)
        cleanup_call = mock_shell.call_args_list[1]
        assert "cleanup" in cleanup_call.args[0]


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
