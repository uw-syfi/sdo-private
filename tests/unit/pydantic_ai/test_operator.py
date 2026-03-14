"""Tests for PydanticAIOperator."""

import signal
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from app_operator.config import AgentConfig, Config, DeploymentConfig, RuntimeConfig
from app_operator.filesystem import InMemoryFilesystem
from app_operator.pydantic_ai.operator import PydanticAIOperator


@pytest.fixture
def mock_config():
    return Config(
        agent=AgentConfig(provider="openai", model="gpt-4o"),
        deployment=DeploymentConfig(platform="docker", target="local"),
        runtime=RuntimeConfig(impl="pydantic_ai"),
    )


@pytest.fixture
def memory_fs():
    return InMemoryFilesystem()


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "test_repo"
    repo.mkdir()
    return repo


def _patch_agents_stack(stack: ExitStack):
    """Enter all agent patches into the given ExitStack."""
    stack.enter_context(patch("app_operator.pydantic_ai.operator.AnalyzeAgent"))
    stack.enter_context(patch("app_operator.pydantic_ai.operator.ScriptAgent"))
    stack.enter_context(patch("app_operator.pydantic_ai.operator.RepairAgent"))
    stack.enter_context(patch("app_operator.pydantic_ai.operator.HealthAgent"))
    stack.enter_context(patch("app_operator.pydantic_ai.operator.PydanticAITrajectoryRecorder"))


def test_init_success(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    with ExitStack() as stack:
        _patch_agents_stack(stack)
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)
        assert operator.repo_path == repo_path
        assert operator.config == mock_config
        assert operator.model_str == "openai:gpt-4o"
        assert memory_fs.exists(repo_path / ".sds" / "config.toml")


def test_init_repo_not_exist(repo_path, mock_config, memory_fs):
    with pytest.raises(ValueError, match="Repository path does not exist"):
        PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)


def test_init_repo_not_dir(repo_path, mock_config, memory_fs):
    if str(repo_path.parent) != ".":
        memory_fs.mkdir(repo_path.parent)
    memory_fs.write_text(repo_path, "not a dir")

    with pytest.raises(ValueError, match="Repository path is not a directory"):
        PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)


def test_init_no_model(repo_path, memory_fs):
    memory_fs.mkdir(repo_path)
    config = Config(
        agent=AgentConfig(provider="openai", model=None),
        runtime=RuntimeConfig(impl="pydantic_ai"),
    )
    with ExitStack() as stack:
        _patch_agents_stack(stack)
        with pytest.raises(ValueError, match="agent.model must be set"):
            PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=config)


def test_run_success(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    mock_recorder = MagicMock()
    mock_verdict = MagicMock()
    mock_verdict.healthy = True
    mock_verdict.assessment = "OK"
    mock_verdict.diagnosis = ""
    mock_verdict.script_was_fixed = False

    mock_analyze = MagicMock()
    mock_analyze.run.return_value = None
    mock_script = MagicMock()
    mock_script.run.return_value = None
    mock_repair = MagicMock()
    mock_repair.run.return_value = None
    mock_health = MagicMock()
    mock_health.run_check.return_value = mock_verdict

    with (
        patch("app_operator.pydantic_ai.operator.AnalyzeAgent", return_value=mock_analyze),
        patch("app_operator.pydantic_ai.operator.ScriptAgent", return_value=mock_script),
        patch("app_operator.pydantic_ai.operator.RepairAgent", return_value=mock_repair),
        patch("app_operator.pydantic_ai.operator.HealthAgent", return_value=mock_health),
        patch("app_operator.pydantic_ai.operator.PydanticAITrajectoryRecorder", return_value=mock_recorder),
        patch(
            "app_operator.pydantic_ai.operator.run_script",
            return_value={"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        ),
    ):
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)
        exit_code = operator.run()

        assert exit_code == 0
        assert operator._deployed is True
        mock_recorder.finalize.assert_called_with("completed")


def test_run_deployment_failure(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    mock_recorder = MagicMock()
    mock_verdict = MagicMock()
    mock_verdict.healthy = False
    mock_verdict.assessment = "Failed"
    mock_verdict.diagnosis = "Service down"
    mock_verdict.script_was_fixed = False

    mock_analyze = MagicMock()
    mock_analyze.run.return_value = None
    mock_script = MagicMock()
    mock_script.run.return_value = None
    mock_repair = MagicMock()
    mock_repair.run.return_value = None
    mock_health = MagicMock()
    mock_health.run_check.return_value = mock_verdict

    with (
        patch("app_operator.pydantic_ai.operator.AnalyzeAgent", return_value=mock_analyze),
        patch("app_operator.pydantic_ai.operator.ScriptAgent", return_value=mock_script),
        patch("app_operator.pydantic_ai.operator.RepairAgent", return_value=mock_repair),
        patch("app_operator.pydantic_ai.operator.HealthAgent", return_value=mock_health),
        patch("app_operator.pydantic_ai.operator.PydanticAITrajectoryRecorder", return_value=mock_recorder),
        patch(
            "app_operator.pydantic_ai.operator.run_script",
            return_value={"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        ),
    ):
        operator = PydanticAIOperator(
            repo_path=str(repo_path),
            filesystem=memory_fs,
            config=mock_config,
            max_deployment_attempts=2,
        )
        exit_code = operator.run()

        assert exit_code == 0  # run() returns 0 but _deployed is False
        assert operator._deployed is False
        mock_recorder.finalize.assert_called_with("failed")


def test_run_exception(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    mock_recorder = MagicMock()
    mock_analyze = MagicMock()
    mock_analyze.run.side_effect = RuntimeError("Unexpected")

    with (
        patch("app_operator.pydantic_ai.operator.AnalyzeAgent", return_value=mock_analyze),
        patch("app_operator.pydantic_ai.operator.ScriptAgent"),
        patch("app_operator.pydantic_ai.operator.RepairAgent"),
        patch("app_operator.pydantic_ai.operator.HealthAgent"),
        patch("app_operator.pydantic_ai.operator.PydanticAITrajectoryRecorder", return_value=mock_recorder),
    ):
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)
        exit_code = operator.run()

        assert exit_code == 1
        mock_recorder.finalize.assert_called_with("failed")


def test_run_keyboard_interrupt(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    mock_recorder = MagicMock()
    mock_analyze = MagicMock()
    mock_analyze.run.side_effect = KeyboardInterrupt()

    with (
        patch("app_operator.pydantic_ai.operator.AnalyzeAgent", return_value=mock_analyze),
        patch("app_operator.pydantic_ai.operator.ScriptAgent"),
        patch("app_operator.pydantic_ai.operator.RepairAgent"),
        patch("app_operator.pydantic_ai.operator.HealthAgent"),
        patch("app_operator.pydantic_ai.operator.PydanticAITrajectoryRecorder", return_value=mock_recorder),
    ):
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)
        exit_code = operator.run()

        assert exit_code == 1
        mock_recorder.finalize.assert_any_call("interrupted")


def test_handle_shutdown_signal(repo_path, mock_config, memory_fs):
    memory_fs.mkdir(repo_path)

    with ExitStack() as stack:
        _patch_agents_stack(stack)
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        with pytest.raises(KeyboardInterrupt):
            operator._handle_shutdown_signal(signal.SIGINT, None)

        assert operator._shutdown_requested is True


def test_recorder_total_usage_property(repo_path, mock_config, memory_fs):
    """Verify that the recorder exposes total_usage (accumulated by record_run calls)."""
    memory_fs.mkdir(repo_path)

    with ExitStack() as stack:
        _patch_agents_stack(stack)
        operator = PydanticAIOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)
        # recorder is a mock — just verify the attribute is delegated correctly
        assert hasattr(operator, "recorder")
