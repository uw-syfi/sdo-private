from unittest.mock import MagicMock, patch

import pytest

from app_operator.config import AgentConfig, Config, DeploymentConfig, RuntimeConfig
from app_operator.exceptions import AgentError
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.operator import LangGraphOperator


@pytest.fixture
def mock_config():
    return Config(
        agent=AgentConfig(provider="opencode", model="gpt-4"),
        deployment=DeploymentConfig(platform="docker", target="local"),
        runtime=RuntimeConfig(impl="langgraph"),
    )


@pytest.fixture
def memory_fs():
    return InMemoryFilesystem()


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "test_repo"
    repo.mkdir()
    return repo


def test_init_success(repo_path, mock_config, memory_fs):
    """Test successful initialization."""
    memory_fs.mkdir(repo_path)

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph"),
        patch("app_operator.langgraph.operator.TrajectoryRecorder"),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        assert operator.repo_path == repo_path
        assert operator.config == mock_config
        # Check if deployment config was persisted
        assert memory_fs.exists(repo_path / ".sds" / "config.toml")


def test_init_repo_not_exist(repo_path, mock_config, memory_fs):
    """Test init failure when repo does not exist."""
    # Don't create repo in memory_fs

    with pytest.raises(ValueError, match="Repository path does not exist"):
        LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)


def test_init_repo_not_dir(repo_path, mock_config, memory_fs):
    """Test init failure when repo is not a directory."""
    # Ensure parent directory exists in memory_fs
    if str(repo_path.parent) != ".":
        memory_fs.mkdir(repo_path.parent)

    memory_fs.write_text(repo_path, "not a dir")

    with pytest.raises(ValueError, match="Repository path is not a directory"):
        LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)


def test_init_llm_failure(repo_path, mock_config, memory_fs):
    """Test init failure when LLM build fails."""
    memory_fs.mkdir(repo_path)

    with (
        patch(
            "app_operator.langgraph.operator.build_llm",
            side_effect=ValueError("LLM Error"),
        ),
        patch("app_operator.langgraph.operator.TrajectoryRecorder"),
    ):
        with pytest.raises(AgentError, match="Failed to initialize LangGraph LLM"):
            LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)


def test_run_success(repo_path, mock_config, memory_fs):
    """Test successful run execution."""
    memory_fs.mkdir(repo_path)

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "health_result": {"success": True},
        "token_usage": {"total": 100},
    }

    mock_recorder = MagicMock()

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph", return_value=mock_graph),
        patch(
            "app_operator.langgraph.operator.TrajectoryRecorder",
            return_value=mock_recorder,
        ),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        exit_code = operator.run()

        assert exit_code == 0
        assert operator._deployed is True
        mock_graph.invoke.assert_called_once()
        mock_recorder.finalize.assert_called_with("completed")


def test_run_failure(repo_path, mock_config, memory_fs):
    """Test run failure when deployment fails."""
    memory_fs.mkdir(repo_path)

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "health_result": {"success": False},
        "token_usage": {"total": 100},
    }

    mock_recorder = MagicMock()

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph", return_value=mock_graph),
        patch(
            "app_operator.langgraph.operator.TrajectoryRecorder",
            return_value=mock_recorder,
        ),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        exit_code = operator.run()

        assert exit_code == 0  # run returns 0 even if deployment failed, logic says _deployed is False
        assert operator._deployed is False
        mock_recorder.finalize.assert_called_with("failed")


def test_run_exception(repo_path, mock_config, memory_fs):
    """Test run handles unexpected exceptions."""
    memory_fs.mkdir(repo_path)

    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = RuntimeError("Unexpected")

    mock_recorder = MagicMock()

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph", return_value=mock_graph),
        patch(
            "app_operator.langgraph.operator.TrajectoryRecorder",
            return_value=mock_recorder,
        ),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        exit_code = operator.run()

        assert exit_code == 1
        mock_recorder.finalize.assert_called_with("failed")


def test_run_keyboard_interrupt(repo_path, mock_config, memory_fs):
    """Test run handles KeyboardInterrupt."""
    memory_fs.mkdir(repo_path)

    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = KeyboardInterrupt()

    mock_recorder = MagicMock()

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph", return_value=mock_graph),
        patch(
            "app_operator.langgraph.operator.TrajectoryRecorder",
            return_value=mock_recorder,
        ),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        exit_code = operator.run()

        assert exit_code == 1
        # It calls finalize("interrupted") then finally calls finalize("failed")
        # We want to ensure "interrupted" was called.
        mock_recorder.finalize.assert_any_call("interrupted")


def test_handle_shutdown_signal(repo_path, mock_config, memory_fs):
    """Test signal handling."""
    memory_fs.mkdir(repo_path)

    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph"),
        patch("app_operator.langgraph.operator.TrajectoryRecorder"),
    ):
        operator = LangGraphOperator(repo_path=str(repo_path), filesystem=memory_fs, config=mock_config)

        import signal

        with pytest.raises(KeyboardInterrupt):
            operator._handle_shutdown_signal(signal.SIGINT, None)

        assert operator._shutdown_requested is True
