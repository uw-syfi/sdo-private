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
        agent=AgentConfig(provider="codex"),
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
            "app_operator.commands.run.create_agent_from_config",
            side_effect=ValueError("Test error"),
        ):
            exit_code = run_command(mock_args)

    assert exit_code == 1


def test_run_command_returns_1_on_generic_exception(mock_args, mock_config):
    """Test that run_command returns 1 on unexpected exceptions."""
    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_agent_from_config",
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
    mock_agent = MagicMock()

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_agent_from_config",
            return_value=mock_agent,
        ):
            with patch(
                "app_operator.commands.run.AppOperator",
                return_value=mock_operator,
            ) as mock_app_op_class:
                exit_code = run_command(mock_args)

    # Verify operator was created with correct parameters
    mock_app_op_class.assert_called_once()
    call_kwargs = mock_app_op_class.call_args[1]
    assert call_kwargs["repo_path"] == "/test/repo"
    assert call_kwargs["health_check_interval"] == 30
    assert call_kwargs["health_check_max_count"] == 5
    assert call_kwargs["max_deployment_attempts"] == 20
    assert call_kwargs["agent"] == mock_agent
    assert call_kwargs["config"] == mock_config

    # Verify operator.run() was called
    mock_operator.run.assert_called_once()
    assert exit_code == 0


def test_run_command_passes_custom_config_path(mock_config):
    """Test that run_command passes custom config path to load_config."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = "/custom/sds.toml"
    args.tui = False

    mock_agent = MagicMock()
    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch(
        "app_operator.commands.run.load_config", return_value=mock_config
    ) as mock_load:
        with patch(
            "app_operator.commands.run.create_agent_from_config",
            return_value=mock_agent,
        ):
            with patch(
                "app_operator.commands.run.AppOperator",
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
            "app_operator.commands.run.LangGraphOperator",
            return_value=mock_operator,
        ) as mock_lg_class:
            exit_code = run_command(mock_args)

    # Verify LangGraphOperator was created with correct parameters
    mock_lg_class.assert_called_once()
    call_kwargs = mock_lg_class.call_args[1]
    assert call_kwargs["repo_path"] == "/test/repo"
    assert call_kwargs["health_check_interval"] == 30
    assert call_kwargs["health_check_max_count"] == 5
    assert call_kwargs["max_deployment_attempts"] == 20
    assert call_kwargs["config"] == langgraph_config

    # Verify operator.run() was called
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
            "app_operator.commands.run.AdkOperator", return_value=mock_operator
        ) as mock_adk_class:
            exit_code = run_command(mock_args)

    # Verify AdkOperator was created with correct parameters
    mock_adk_class.assert_called_once()
    call_kwargs = mock_adk_class.call_args[1]
    assert call_kwargs["repo_path"] == "/test/repo"
    assert call_kwargs["health_check_interval"] == 30
    assert call_kwargs["health_check_max_count"] == 5
    assert call_kwargs["max_deployment_attempts"] == 20
    assert call_kwargs["config"] == adk_config

    # Verify operator.run() was called
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

    mock_app = MagicMock()
    mock_app._exit_code = 0
    mock_agent = MagicMock()

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_agent_from_config",
            return_value=mock_agent,
        ):
            # OperatorTUI is imported locally, so patch where it's used
            with patch(
                "app_operator.ui.textual_tui.OperatorTUI",
                return_value=mock_app,
            ) as mock_tui_class:
                exit_code = run_command(args)

    # Verify TUI was created and run
    mock_tui_class.assert_called_once()
    mock_app.run.assert_called_once()
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
            "app_operator.commands.run.LangGraphOperator",
            return_value=mock_operator,
        ):
            # OperatorTUI is imported locally, patch where it's used
            with patch(
                "app_operator.ui.textual_tui.OperatorTUI"
            ) as mock_tui_class:
                exit_code = run_command(mock_args)

    # TUI should NOT be created for non-cli_agent runtime
    mock_tui_class.assert_not_called()
    # Regular operator should run instead
    mock_operator.run.assert_called_once()
    assert exit_code == 0


def test_run_command_tui_operator_factory(mock_config):
    """Test that TUI operator factory creates operator correctly."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    args.tui = True

    mock_app = MagicMock()
    mock_app._exit_code = 0
    mock_agent = MagicMock()
    mock_ui = MagicMock()

    # Track the factory function passed to TUI
    captured_factory = None

    def capture_factory(factory):
        nonlocal captured_factory
        captured_factory = factory
        return mock_app

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_agent_from_config",
            return_value=mock_agent,
        ):
            # OperatorTUI is imported locally, patch where it's used
            with patch(
                "app_operator.ui.textual_tui.OperatorTUI",
                side_effect=capture_factory,
            ):
                with patch(
                    "app_operator.commands.run.AppOperator"
                ) as mock_app_op_class:
                    run_command(args)

                    # Call the captured factory function
                    captured_factory(mock_ui)

    # Verify operator was created with UI
    mock_app_op_class.assert_called_once()
    call_kwargs = mock_app_op_class.call_args[1]
    assert call_kwargs["ui"] == mock_ui
    assert call_kwargs["agent"] == mock_agent


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
        agent=AgentConfig(provider="codex"),
        operator=OperatorConfig(
            interval=60,  # Custom interval
            monitoring_max_iters=10,  # Custom monitoring
            deployment_max_iters=15,  # Custom deployment
        ),
        runtime=RuntimeConfig(impl="cli_agent"),
    )

    mock_agent = MagicMock()
    mock_operator = MagicMock()
    mock_operator.run.return_value = 0

    with patch("app_operator.commands.run.load_config", return_value=custom_config):
        with patch(
            "app_operator.commands.run.create_agent_from_config",
            return_value=mock_agent,
        ):
            with patch(
                "app_operator.commands.run.AppOperator",
                return_value=mock_operator,
            ) as mock_app_op_class:
                run_command(args)

    # Verify custom values were passed
    call_kwargs = mock_app_op_class.call_args[1]
    assert call_kwargs["health_check_interval"] == 60
    assert call_kwargs["health_check_max_count"] == 10
    assert call_kwargs["max_deployment_attempts"] == 15
