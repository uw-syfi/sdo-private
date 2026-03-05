"""Unit tests for app_operator.commands.run module."""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from app_operator.commands.run import add_arguments, run_command
from app_operator.config import Config, AgentConfig, OperatorConfig, RuntimeConfig


@pytest.fixture
def mock_args():
    """Create mock arguments for testing."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    args.tui = False
    return args


@pytest.fixture
def mock_config():
    """Create a mock Config object."""
    return Config(
        agent=AgentConfig(provider="codex", model="test-model"),
        operator=OperatorConfig(
            interval=30,
            monitoring_max_iters=5,
            deployment_max_iters=20,
        ),
        runtime=RuntimeConfig(impl="cli_agent"),
    )

# ============================================================================
# add_arguments Tests
# ============================================================================


def test_add_arguments_adds_directory():
    """Test that add_arguments adds directory argument."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Parse with directory
    args = parser.parse_args(["/some/path"])
    assert args.directory == "/some/path"


def test_add_arguments_adds_config_flag():
    """Test that add_arguments adds --config flag."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Parse with config
    args = parser.parse_args(["/path", "--config", "custom.toml"])
    assert args.config == "custom.toml"


def test_add_arguments_adds_tui_flag():
    """Test that add_arguments adds --tui flag."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Default should be False
    args = parser.parse_args(["/path"])
    assert args.tui is False

    # With --tui should be True
    args = parser.parse_args(["/path", "--tui"])
    assert args.tui is True


def test_add_arguments_config_is_optional():
    """Test that --config flag is optional."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Should work without --config
    args = parser.parse_args(["/path"])
    assert args.config is None

# ============================================================================
# run_command Tests - Error Cases
# ============================================================================


def test_run_command_returns_1_when_directory_empty(mock_config):
    """Test that run_command returns 1 when directory is empty."""
    args = argparse.Namespace()
    args.directory = ""
    args.config = None
    args.tui = False

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        exit_code = run_command(args)

    assert exit_code == 1


def test_run_command_returns_1_when_interval_less_than_1(mock_args, mock_config):
    """Test that run_command returns 1 when interval is less than 1."""
    # Modify the config to have invalid interval after creation
    mock_config.operator.interval = 0  # Set to invalid value

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        exit_code = run_command(mock_args)

    assert exit_code == 1


def test_run_command_returns_1_on_value_error(mock_args, mock_config):
    """Test that run_command returns 1 when ValueError is raised."""
    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_operator",
            side_effect=ValueError("Test error"),
        ):
            exit_code = run_command(mock_args)

    assert exit_code == 1


def test_run_command_returns_1_on_generic_exception(mock_args, mock_config):
    """Test that run_command returns 1 on unexpected exceptions."""
    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_operator",
            side_effect=RuntimeError("Unexpected error"),
        ):
            exit_code = run_command(mock_args)

    assert exit_code == 1

# ============================================================================
# run_command Tests - cli_agent Runtime
# ============================================================================


def test_run_command_runs_cli_agent_operator(mock_args, mock_config):
    """Test that run_command creates and runs AppOperator for cli_agent."""
    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            exit_code = run_command(mock_args)

    # Verify create_operator was called
    mock_create.assert_called_once()
    # Verify operator.run() was called
    mock_operator.run.assert_called_once()
    assert exit_code == 0


def test_run_command_passes_custom_config_path(mock_config):
    """Test that run_command passes custom config path to load_config."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = "/custom/sds.toml"
    args.tui = False

    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch(
        "app_operator.commands.run.load_config", return_value=mock_config
    ) as mock_load:
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ):
            run_command(args)

    # Verify custom config path was passed
    mock_load.assert_called_once_with("/test/repo", "/custom/sds.toml")

# ============================================================================
# run_command Tests - langgraph Runtime
# ============================================================================


def test_run_command_runs_langgraph_operator(mock_args):
    """Test that run_command creates and runs LangGraphOperator."""
    langgraph_config = Config(
        agent=AgentConfig(provider="gemini", model="gemini-1.5-pro"),
        operator=OperatorConfig(
            interval=30,
            monitoring_max_iters=5,
            deployment_max_iters=20,
        ),
        runtime=RuntimeConfig(impl="langgraph"),
    )

    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch(
        "app_operator.commands.run.load_config", return_value=langgraph_config
    ):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            exit_code = run_command(mock_args)

    # Verify create_operator was called and returned expected result
    mock_create.assert_called_once()
    mock_operator.run.assert_called_once()
    assert exit_code == 0

# ============================================================================
# run_command Tests - adk Runtime
# ============================================================================


def test_run_command_runs_adk_operator(mock_args):
    """Test that run_command creates and runs AdkOperator."""
    adk_config = Config(
        agent=AgentConfig(provider="gemini", model="gemini-1.5-pro"),
        operator=OperatorConfig(
            interval=30,
            monitoring_max_iters=5,
            deployment_max_iters=20,
        ),
        runtime=RuntimeConfig(impl="adk"),
    )

    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch("app_operator.commands.run.load_config", return_value=adk_config):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            exit_code = run_command(mock_args)

    # Verify create_operator was called and returned expected result
    mock_create.assert_called_once()
    mock_operator.run.assert_called_once()
    assert exit_code == 0

# ============================================================================
# run_command Tests - TUI Mode
# ============================================================================


def test_run_command_enables_tui_for_cli_agent(mock_config):
    """Test that run_command enables TUI for cli_agent runtime."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    args.tui = True

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_tui_app",
            return_value=0,
        ) as mock_tui:
            exit_code = run_command(args)

    # Verify TUI was invoked
    mock_tui.assert_called_once()
    assert exit_code == 0


def test_run_command_disables_tui_for_non_cli_agent(mock_args):
    """Test that run_command disables TUI for non-cli_agent runtimes."""
    langgraph_config = Config(
        agent=AgentConfig(provider="gemini", model="gemini-1.5-pro"),
        operator=OperatorConfig(
            interval=30,
            monitoring_max_iters=5,
            deployment_max_iters=20,
        ),
        runtime=RuntimeConfig(impl="langgraph"),
    )

    mock_args.tui = True  # Request TUI but runtime doesn't support it

    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch(
        "app_operator.commands.run.load_config", return_value=langgraph_config
    ):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ):
            with patch(
                "app_operator.commands.run.create_tui_app"
            ) as mock_tui:
                exit_code = run_command(mock_args)

    # TUI should NOT be invoked for non-cli_agent runtime
    mock_tui.assert_not_called()
    # Regular operator should run instead
    mock_operator.run.assert_called_once()
    assert exit_code == 0


def test_run_command_tui_operator_factory(mock_config):
    """Test that TUI path delegates to create_tui_app with correct args."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    args.tui = True

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_tui_app",
            return_value=0,
        ) as mock_tui:
            exit_code = run_command(args)

    # Verify create_tui_app was called with shared_kwargs and config
    mock_tui.assert_called_once()
    call_args = mock_tui.call_args
    shared_kwargs = call_args[0][0]
    config_arg = call_args[0][1]
    assert shared_kwargs["repo_path"] == "/test/repo"
    assert shared_kwargs["health_check_interval"] == 30
    assert config_arg == mock_config
    assert exit_code == 0

# ============================================================================
# run_command Tests - Configuration Values
# ============================================================================


def test_run_command_uses_config_intervals():
    """Test that run_command uses configuration values correctly."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    args.tui = False

    custom_config = Config(
        agent=AgentConfig(provider="codex", model="test-model"),
        operator=OperatorConfig(
            interval=60,  # Custom interval
            monitoring_max_iters=10,  # Custom monitoring
            deployment_max_iters=15,  # Custom deployment
        ),
        runtime=RuntimeConfig(impl="cli_agent"),
    )

    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch("app_operator.commands.run.load_config", return_value=custom_config):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            run_command(args)

    # Verify shared_kwargs were passed with correct values
    call_args = mock_create.call_args
    shared_kwargs = call_args[0][0]
    assert shared_kwargs["health_check_interval"] == 60
    assert shared_kwargs["health_check_max_count"] == 10
    assert shared_kwargs["max_deployment_attempts"] == 15
