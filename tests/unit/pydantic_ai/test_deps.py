"""Tests for OperatorDeps."""

import pytest

from app_operator.config import AgentConfig, Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.prompts import PromptLoader
from app_operator.pydantic_ai._deps import OperatorDeps


@pytest.fixture
def deps(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return OperatorDeps(
        repo_path=repo,
        filesystem=InMemoryFilesystem(),
        loader=PromptLoader(),
        config=Config(agent=AgentConfig(provider="openai", model="gpt-4o")),
    )


def test_resolve_relative_path(deps):
    result = deps.resolve_path("src/main.py")
    assert result == deps.repo_path / "src" / "main.py"


def test_resolve_absolute_path_inside_repo(deps):
    abs_path = str(deps.repo_path / "file.txt")
    result = deps.resolve_path(abs_path)
    assert result == deps.repo_path / "file.txt"


def test_resolve_path_rejects_escape(deps):
    with pytest.raises(ValueError, match="Path escapes repository root"):
        deps.resolve_path("../../etc/passwd")


def test_resolve_path_normalizes_dotdot(deps):
    result = deps.resolve_path("src/../file.txt")
    assert result == deps.repo_path / "file.txt"


def test_should_shutdown_false_by_default(deps):
    assert deps.should_shutdown() is False


def test_should_shutdown_with_callback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    deps = OperatorDeps(
        repo_path=repo,
        filesystem=InMemoryFilesystem(),
        loader=PromptLoader(),
        config=Config(agent=AgentConfig(provider="openai", model="gpt-4o")),
        check_shutdown=lambda: True,
    )
    assert deps.should_shutdown() is True


def test_next_tool_call_id_increments(deps):
    assert deps.next_tool_call_id() == 1
    assert deps.next_tool_call_id() == 2
    assert deps.next_tool_call_id() == 3
