"""Tests for deploy_attempt node function."""

from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.nodes.deployer import deploy_attempt
from app_operator.langgraph.state import OperatorState
from app_operator.trajectory import NullTrajectoryRecorder, Phase


class TestDeployAttempt:
    """Test deploy_attempt node function."""

    def test_deploy_attempt_successful(self):
        """Test deploy_attempt with successful deployment."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config()
        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.start_phase = Mock()

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0, "stdout": "Deployment successful"}

            result_state = deploy_attempt(state, repo_path, filesystem, config, check_shutdown, recorder)

        assert result_state["deploy_result"]["exit_code"] == 0
        recorder.start_phase.assert_called_once_with(Phase.DEPLOYMENT, {"attempt": 1, "max_attempts": 3})

    def test_deploy_attempt_with_failure(self):
        """Test deploy_attempt with failed deployment."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config()
        check_shutdown = Mock(return_value=False)

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 1, "stderr": "Deployment failed"}

            result_state = deploy_attempt(state, repo_path, filesystem, config, check_shutdown)

        assert result_state["deploy_result"]["exit_code"] == 1

    def test_deploy_attempt_shutdown_check(self):
        """Test deploy_attempt respects shutdown flag."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config()
        check_shutdown = Mock(return_value=True)

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            result_state = deploy_attempt(state, repo_path, filesystem, config, check_shutdown)

        # Should not run script when shutdown is True
        mock_run.assert_not_called()
        assert result_state == state

    def test_deploy_attempt_none_shutdown_check(self):
        """Test deploy_attempt with None shutdown check."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config()

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0}

            deploy_attempt(state, repo_path, filesystem, config, check_shutdown=None)

        # Should run script when shutdown check is None
        mock_run.assert_called_once()

    def test_deploy_attempt_uses_correct_log_path(self):
        """Test deploy_attempt uses correct log file path."""
        state = OperatorState(
            attempt=2,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            health_result=None,
            last_fix_summary=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config()

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0}

            deploy_attempt(state, repo_path, filesystem, config, None)

        # Verify log path includes attempt number
        call_args = mock_run.call_args
        log_path = call_args[1]["log_file_path"]
        assert "deploy_attempt_2.log" in str(log_path)
