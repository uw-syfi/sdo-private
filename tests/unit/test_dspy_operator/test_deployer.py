"""Tests for app_operator_dspy.agents.deployer."""

from unittest.mock import MagicMock, patch

import dspy

from app_operator_dspy.agents.deployer import DeploymentAgent


def _mock_agent(deploy_script="#!/bin/bash\nexit 0", health_script="#!/bin/bash\nexit 0"):
    agent = DeploymentAgent()
    agent.gen_deploy = MagicMock(return_value=dspy.Prediction(deploy_script=deploy_script))
    agent.gen_health = MagicMock(return_value=dspy.Prediction(health_check_script=health_script))
    agent.diagnose = MagicMock(return_value=dspy.Prediction(diagnosis="port conflict", fix_plan="change port"))
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
        assert agent.diagnose.call_count == 0

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_deploy_fails_then_succeeds(self, mock_shell, tmp_path):
        # First attempt: deploy fails. Second attempt: both succeed.
        mock_shell.side_effect = [
            "Exit code: 1\nStderr:\nport in use",  # deploy fail
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 0\nStdout:\nhealthy",  # health success
        ]
        agent = _mock_agent()

        result = agent.forward(str(tmp_path), "analysis", "issues", max_attempts=3)

        assert result.success is True
        assert result.attempts == 2
        assert agent.diagnose.call_count == 1

    @patch("app_operator_dspy.agents.deployer.run_shell")
    def test_health_fails_then_succeeds(self, mock_shell, tmp_path):
        mock_shell.side_effect = [
            "Exit code: 0\nStdout:\nOK",  # deploy success
            "Exit code: 1\nStderr:\nservice down",  # health fail
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
