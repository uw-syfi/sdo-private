"""Unit tests for app_operator.commands.run module."""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from app_operator.commands.run import EXIT_INTERRUPTED, add_arguments, run_command
from app_operator.core import AgentConfig, Config, DeploymentError, MonitoringError, OperatorConfig, RuntimeConfig
from libs.model_config import ModelConfig


@pytest.fixture
def mock_args():
    """Create mock arguments for testing."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    return args


@pytest.fixture
def mock_config():
    """Create a mock Config object."""
    return Config(
        agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
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


def test_add_arguments_config_is_optional():
    """Test that --config flag is optional."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    # Should work without --config
    args = parser.parse_args(["/path"])
    assert args.config is None


def test_add_arguments_adds_verbose_flag():
    """Test that --verbose defaults to false and can be enabled."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)

    assert parser.parse_args(["/path"]).verbose is False
    assert parser.parse_args(["/path", "--verbose"]).verbose is True


# ============================================================================
# run_command Tests - Error Cases
# ============================================================================


def test_run_command_returns_1_when_directory_empty(mock_config):
    """Test that run_command returns 1 when directory is empty."""
    args = argparse.Namespace()
    args.directory = ""
    args.config = None
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
    mock_operator.run.return_value = None  # operator.run() returns None on success

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            exit_code = run_command(mock_args)

    # Verify create_operator was called
    mock_create.assert_called_once()
    assert mock_create.call_args.args[0]["verbose"] is False
    # Verify operator.run() was called
    mock_operator.run.assert_called_once()
    assert exit_code == 0


def test_run_command_passes_verbose_to_operator(mock_args, mock_config):
    """Test that run --verbose is forwarded through shared operator kwargs."""
    mock_args.verbose = True
    mock_operator = MagicMock()
    mock_operator.run.return_value = None

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ) as mock_create:
            exit_code = run_command(mock_args)

    assert exit_code == 0
    assert mock_create.call_args.args[0]["verbose"] is True


def test_run_command_returns_1_on_deployment_error(mock_args, mock_config):
    """DeploymentError from operator → exit code 1."""
    mock_operator = MagicMock()
    mock_operator.run.side_effect = DeploymentError("deploy failed", attempt=3)

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch("app_operator.commands.run.create_operator", return_value=mock_operator):
            exit_code = run_command(mock_args)

    assert exit_code == 1


def test_run_command_returns_1_on_monitoring_error(mock_args, mock_config):
    """MonitoringError (deployed but unhealthy) → exit code 1."""
    mock_operator = MagicMock()
    mock_operator.run.side_effect = MonitoringError("unhealthy after deploy")

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch("app_operator.commands.run.create_operator", return_value=mock_operator):
            exit_code = run_command(mock_args)

    assert exit_code == 1


def test_run_command_returns_130_on_keyboard_interrupt(mock_args, mock_config):
    """KeyboardInterrupt → POSIX exit code 130 (128 + SIGINT)."""
    mock_operator = MagicMock()
    mock_operator.run.side_effect = KeyboardInterrupt()

    with patch("app_operator.commands.run.load_config", return_value=mock_config):
        with patch("app_operator.commands.run.create_operator", return_value=mock_operator):
            exit_code = run_command(mock_args)

    assert exit_code == EXIT_INTERRUPTED == 130


def test_run_command_passes_custom_config_path(mock_config):
    """Test that run_command passes custom config path to load_config."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = "/custom/sds.toml"
    mock_operator = MagicMock()
    mock_operator.run.return_value = None

    with patch("app_operator.commands.run.load_config", return_value=mock_config) as mock_load:
        with patch(
            "app_operator.commands.run.create_operator",
            return_value=mock_operator,
        ):
            run_command(args)

    # Verify custom config path was passed
    mock_load.assert_called_once_with("/test/repo", "/custom/sds.toml")


# ============================================================================
# run_command Tests - Configuration Values
# ============================================================================


def test_run_command_uses_config_intervals():
    """Test that run_command uses configuration values correctly."""
    args = argparse.Namespace()
    args.directory = "/test/repo"
    args.config = None
    custom_config = Config(
        agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
        operator=OperatorConfig(
            interval=60,  # Custom interval
            monitoring_max_iters=10,  # Custom monitoring
            deployment_max_iters=15,  # Custom deployment
        ),
        runtime=RuntimeConfig(impl="cli_agent"),
    )

    mock_operator = MagicMock()
    mock_operator.run.return_value = None

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
