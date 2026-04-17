"""Shared fixtures for pydantic_ai tests."""

import pytest

from app_operator.core import AgentConfig, Config, DeploymentConfig, InMemoryFilesystem, RuntimeConfig
from libs.model_config import ModelConfig


@pytest.fixture
def mock_config():
    return Config(
        agent=AgentConfig(backend="openai", model_config=ModelConfig(provider="openai", model="gpt-4o")),
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
