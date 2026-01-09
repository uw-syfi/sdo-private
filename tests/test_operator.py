import pytest
from unittest.mock import Mock, patch
import signal
from app_operator.operator import AppOperator


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
    with patch('app_operator.operator.DeploymentAgent') as mock_deployer_cls, \
            patch('app_operator.operator.AppMonitor') as mock_monitor_cls:

        op = AppOperator(str(repo_path), agent=mock_agent)
        yield op, mock_deployer_cls.return_value, mock_monitor_cls.return_value


def test_operator_init_validates_path(tmp_path):
    with patch('app_operator.operator.create_agent_from_config') as mock_create_agent:
        mock_create_agent.return_value = Mock()
        with pytest.raises(ValueError, match="does not exist"):
            AppOperator(str(tmp_path / "nonexistent"))


def test_run_success_flow(app_operator):
    op, mock_deployer, mock_monitor = app_operator

    # Setup mocks
    mock_deployer.run.return_value = True

    # Stop the monitor loop immediately
    mock_monitor.run.side_effect = None

    exit_code = op.run()

    assert exit_code == 0
    mock_deployer.run.assert_called_once()
    mock_monitor.run.assert_called_once()


def test_run_deployment_failure(app_operator):
    op, mock_deployer, mock_monitor = app_operator

    # Deployment fails
    mock_deployer.run.return_value = False

    exit_code = op.run()

    assert exit_code == 1
    mock_deployer.run.assert_called_once()
    mock_monitor.run.assert_not_called()


def test_cleanup_stops_application(app_operator):
    op, mock_deployer, _ = app_operator

    # Pretend we deployed successfully
    op._deployed = True
    mock_deployer.run_deploy_command.return_value = {"success": True, "exit_code": 0}

    op._cleanup()

    mock_deployer.run_deploy_command.assert_called_with("stop", timeout=120)


def test_cleanup_skips_if_not_deployed(app_operator):
    op, mock_deployer, _ = app_operator

    op._deployed = False

    op._cleanup()

    mock_deployer.run_deploy_command.assert_not_called()


def test_handle_shutdown_signal(app_operator):
    op, _, _ = app_operator

    # Verify initial state
    assert op._shutdown_requested is False

    # Simulate signal
    op._handle_shutdown_signal(signal.SIGINT, None)

    assert op._shutdown_requested is True


def test_run_handles_exception_gracefully(app_operator):
    op, mock_deployer, _ = app_operator

    mock_deployer.run.side_effect = Exception("Unexpected crash")

    exit_code = op.run()

    assert exit_code == 1
