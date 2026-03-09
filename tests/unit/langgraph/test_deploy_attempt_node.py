"""Tests for deploy_attempt node function."""

from pathlib import Path
from unittest.mock import Mock, patch

from app_operator.config import AgentConfig, Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.nodes.deployer import deploy_attempt
from app_operator.langgraph.state import OperatorState
from app_operator.trajectory import NullTrajectoryRecorder, Phase


def _make_ctx(
    repo_path: Path,
    filesystem,
    config: Config,
    check_shutdown=None,
    recorder=None,
) -> NodeContext:
    return NodeContext(
        repo_path=repo_path,
        filesystem=filesystem,
        loader=Mock(),
        config=config,
        context_limit=128000,
        recorder=recorder or NullTrajectoryRecorder(),
        check_shutdown=check_shutdown,
    )


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
            last_fix_summary=None,
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        check_shutdown = Mock(return_value=False)
        recorder = Mock(spec=NullTrajectoryRecorder())
        recorder.start_phase = Mock()
        ctx = _make_ctx(repo_path, filesystem, config, check_shutdown, recorder)

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0, "stdout": "Deployment successful"}

            result_state = deploy_attempt(state, ctx)

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
            last_fix_summary=None,
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, filesystem, config, check_shutdown=Mock(return_value=False))

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 1, "stderr": "Deployment failed"}

            result_state = deploy_attempt(state, ctx)

        assert result_state["deploy_result"]["exit_code"] == 1

    def test_deploy_attempt_shutdown_check(self):
        """Test deploy_attempt respects shutdown flag."""
        state = OperatorState(
            attempt=1,
            max_attempts=3,
            messages=[],
            scripts_done=True,
            deploy_result=None,
            last_fix_summary=None,
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, filesystem, config, check_shutdown=Mock(return_value=True))

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            result_state = deploy_attempt(state, ctx)

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
            last_fix_summary=None,
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, filesystem, config, check_shutdown=None)

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0}

            deploy_attempt(state, ctx)

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
            last_fix_summary=None,
            health_verdict=None,
        )

        repo_path = Path("/test/repo")
        filesystem = InMemoryFilesystem()
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        ctx = _make_ctx(repo_path, filesystem, config)

        with patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run:
            mock_run.return_value = {"exit_code": 0}

            deploy_attempt(state, ctx)

        # Verify log path includes attempt number
        call_args = mock_run.call_args
        log_path = call_args[1]["log_file_path"]
        assert "deploy_attempt_2.log" in str(log_path)
