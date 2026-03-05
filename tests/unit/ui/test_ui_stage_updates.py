import pytest
from unittest.mock import Mock, patch
from app_operator.cli_agent.operator import AppOperator
from app_operator.config import AgentConfig, Config
from app_operator.ui import OperatorUI


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def mock_agent():
    return Mock()


@pytest.fixture
def mock_ui():
    return Mock(spec=OperatorUI)


@pytest.fixture
def app_operator_with_ui(repo_path, mock_agent, mock_ui):
    with (
        patch("app_operator.cli_agent.operator.DeploymentAgent") as mock_deployer_cls,
        patch("app_operator.cli_agent.operator.AppMonitor") as mock_monitor_cls,
        patch("app_operator.cli_agent.operator.CodeAnalyzerAgent") as mock_analyzer_cls,
    ):
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        op = AppOperator(str(repo_path), agent=mock_agent, ui=mock_ui, config=config)
        yield (
            op,
            mock_deployer_cls,
            mock_monitor_cls,
            mock_analyzer_cls,
            mock_ui,
        )


def test_run_success_flow_updates_ui_stages(app_operator_with_ui):
    op, mock_deployer_cls, mock_monitor_cls, mock_analyzer_cls, mock_ui = (
        app_operator_with_ui
    )

    # Setup mocks
    mock_deployer_cls.return_value.run.return_value = True
    mock_monitor_cls.return_value.run.side_effect = None

    exit_code = op.run()

    assert exit_code == 0

    # Verify agents were initialized with ui
    # We check that ui kwarg was passed
    assert mock_analyzer_cls.call_args[1]["ui"] == mock_ui
    assert mock_deployer_cls.call_args[1]["ui"] == mock_ui
    assert mock_monitor_cls.call_args[1]["ui"] == mock_ui

    # Verify agent event handler attached
    assert op.agent.event_handler == mock_ui
