"""Shared fixtures for pydantic_ai tests."""

import pytest

from app_operator.config import AgentConfig, Config, DeploymentConfig, RuntimeConfig
from app_operator.filesystem import InMemoryFilesystem


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
