import signal
from unittest.mock import Mock, patch

import pytest

from app_operator.cli_agent.operator import AppOperator
from app_operator.core import AgentConfig, Config, MonitoringError, OperatorUI
from libs.model_config import ModelConfig


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def mock_agent():
    return Mock()


@pytest.fixture
def app_operator(repo_path, mock_agent):
    # Mock the internal agents to avoid real instantiation
    with (
        patch("app_operator.cli_agent.operator.DeploymentAgent") as mock_deployer_cls,
        patch("app_operator.cli_agent.operator.AppMonitor") as mock_monitor_cls,
        patch("app_operator.cli_agent.operator.CodeAnalyzerAgent") as mock_analyzer_cls,
    ):
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        op = AppOperator(str(repo_path), agent=mock_agent, config=config)
        yield (
            op,
            mock_deployer_cls.return_value,
            mock_monitor_cls.return_value,
            mock_analyzer_cls.return_value,
        )


def test_operator_init_validates_path(tmp_path):
    with patch("app_operator.cli_agent.operator.create_agent_from_config") as mock_create_agent:
        mock_create_agent.return_value = Mock()
        with pytest.raises(ValueError, match="does not exist"):
            AppOperator(str(tmp_path / "nonexistent"))


def test_run_success_flow(app_operator):
    op, mock_deployer, mock_monitor, mock_analyzer = app_operator

    # Setup mocks: deployer succeeds, monitor reports healthy
    mock_deployer.run.return_value = True
    mock_monitor.run.side_effect = None
    mock_monitor.healthy = True

    # Success → returns normally (no exception)
    op.run()

    mock_analyzer.run.assert_called_once()
    mock_deployer.run.assert_called_once()
    mock_monitor.run.assert_called_once()


def test_run_deployment_shutdown_returns_quietly(app_operator):
    """Deployer returning False means shutdown was requested — exit quietly."""
    op, mock_deployer, mock_monitor, mock_analyzer = app_operator

    mock_deployer.run.return_value = False

    # Shutdown case returns normally (no exception)
    op.run()

    mock_analyzer.run.assert_called_once()
    mock_deployer.run.assert_called_once()
    mock_monitor.run.assert_not_called()


def test_run_unhealthy_monitor_raises_monitoring_error(app_operator):
    """When monitor reports unhealthy after deploy, raise MonitoringError."""
    op, mock_deployer, mock_monitor, _ = app_operator

    mock_deployer.run.return_value = True
    mock_monitor.run.side_effect = None
    mock_monitor.healthy = False

    with pytest.raises(MonitoringError):
        op.run()


def test_cleanup_stops_application(app_operator):
    op, mock_deployer, _, _ = app_operator

    # Pretend we deployed successfully
    op._deployed = True
    mock_deployer.run_deploy_command.return_value = {"success": True, "exit_code": 0}

    op._cleanup()

    mock_deployer.run_deploy_command.assert_called_with("stop", timeout=120)


def test_cleanup_skips_if_not_deployed(app_operator):
    op, mock_deployer, _, _ = app_operator

    op._deployed = False

    op._cleanup()

    mock_deployer.run_deploy_command.assert_not_called()


def test_handle_shutdown_signal_sigint(app_operator):
    op, _, _, _ = app_operator

    # Verify initial state
    assert op._shutdown_requested is False

    # SIGINT should set the flag but NOT raise KeyboardInterrupt
    # (raising from a signal handler is dangerous in multi-threaded code)
    op._handle_shutdown_signal(signal.SIGINT, None)

    assert op._shutdown_requested is True


def test_handle_shutdown_signal_sigterm(app_operator):
    op, _, _, _ = app_operator

    # Verify initial state
    assert op._shutdown_requested is False

    # Simulate SIGTERM signal - should NOT raise KeyboardInterrupt
    op._handle_shutdown_signal(signal.SIGTERM, None)

    assert op._shutdown_requested is True


def test_run_propagates_keyboard_interrupt(app_operator):
    """KeyboardInterrupt should propagate; cleanup still runs via finally."""
    op, mock_deployer, _, _ = app_operator

    # Simulate KeyboardInterrupt during deployment
    mock_deployer.run.side_effect = KeyboardInterrupt()

    # Mock _cleanup to verify it's called from the finally block
    with patch.object(op, "_cleanup") as mock_cleanup:
        with pytest.raises(KeyboardInterrupt):
            op.run()
        mock_cleanup.assert_called_once()


def test_run_propagates_unexpected_exceptions(app_operator):
    """Unexpected exceptions propagate to the CLI boundary for translation."""
    op, mock_deployer, _, _ = app_operator

    mock_deployer.run.side_effect = RuntimeError("Unexpected crash")

    with pytest.raises(RuntimeError, match="Unexpected crash"):
        op.run()


def test_run_monitor_failure_marks_failed_status(repo_path, mock_agent):
    """When monitor raises, finally-block still marks status as failed."""
    mock_ui = Mock(spec=OperatorUI)

    with (
        patch("app_operator.cli_agent.operator.DeploymentAgent") as mock_deployer_cls,
        patch("app_operator.cli_agent.operator.AppMonitor") as mock_monitor_cls,
        patch("app_operator.cli_agent.operator.CodeAnalyzerAgent"),
    ):
        mock_deployer_cls.return_value.run.return_value = True
        mock_monitor_cls.return_value.run.side_effect = RuntimeError("monitor failed")

        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        op = AppOperator(str(repo_path), agent=mock_agent, ui=mock_ui, config=config)
        with patch.object(op.recorder, "finalize") as mock_finalize:
            with pytest.raises(RuntimeError, match="monitor failed"):
                op.run()

    mock_ui.close.assert_called_once_with(status="failed", exit_code=1)
    mock_finalize.assert_called_once_with("failed")
