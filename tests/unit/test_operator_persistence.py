from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app_operator.cli_agent.operator import AppOperator
from app_operator.config import AgentConfig, Config, DeploymentConfig
from app_operator.filesystem import InMemoryFilesystem
from libs.model_config import ModelConfig
from tests.fixtures.agents import StubAgent


@pytest.fixture
def mock_trajectory():
    with patch("app_operator.cli_agent.operator.TrajectoryRecorder") as mock:
        mock_recorder = MagicMock()
        mock.return_value = mock_recorder
        yield mock


def test_operator_persists_config(mock_trajectory):
    fs = InMemoryFilesystem()
    repo_path = Path("/tmp/repo")
    fs.mkdir(repo_path)

    agent = StubAgent(model="test")

    config = Config(
        agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
        deployment=DeploymentConfig(platform="k8s", target="local"),
    )

    _ = AppOperator(repo_path=str(repo_path), filesystem=fs, agent=agent, config=config)

    sds_config = repo_path / ".sds" / "config.toml"
    assert fs.exists(sds_config)
    content = fs.read_text(sds_config)
    assert "[deployment]" in content
    assert 'platform = "k8s"' in content
    assert 'target = "local"' in content


def test_operator_does_not_overwrite_existing_config(mock_trajectory):
    fs = InMemoryFilesystem()
    repo_path = Path("/tmp/repo")
    fs.mkdir(repo_path)
    fs.mkdir(repo_path / ".sds")

    # Pre-existing config
    sds_config = repo_path / ".sds" / "config.toml"
    fs.write_text(sds_config, '[deployment]\nplatform = "docker"\ntarget = "local"\n')

    agent = StubAgent(model="test")

    config = Config(
        agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model")),
        deployment=DeploymentConfig(platform="k8s", target="local"),
    )

    _ = AppOperator(repo_path=str(repo_path), filesystem=fs, agent=agent, config=config)

    content = fs.read_text(sds_config)
    # Should still be docker because file existed and logic is "if not exists"
    assert 'platform = "docker"' in content
