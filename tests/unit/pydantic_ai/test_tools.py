"""Tests for pydantic_ai tool functions."""

from unittest.mock import MagicMock

import pytest

from app_operator.config import AgentConfig, Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.prompts import PromptLoader
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai.tools import (
    OUTPUT_SPILL_THRESHOLD,
    bash,
    build_tools,
    glob_files,
    grep,
    ls_dir,
    read_file,
    str_replace,
    write_file,
)


@pytest.fixture
def deps(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fs = InMemoryFilesystem()
    fs.mkdir(repo)
    return OperatorDeps(
        repo_path=repo,
        filesystem=fs,
        loader=PromptLoader(),
        config=Config(agent=AgentConfig(provider="openai", model="gpt-4o")),
    )


def _ctx(deps):
    """Create a minimal RunContext-like mock."""
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


class TestLsDir:
    def test_list_directory(self, deps):
        deps.filesystem.mkdir(deps.repo_path / "src")
        deps.filesystem.write_text(deps.repo_path / "file.txt", "hello")
        result = ls_dir(_ctx(deps), ".")
        assert "file.txt" in result
        assert "src" in result

    def test_nonexistent_path(self, deps):
        result = ls_dir(_ctx(deps), "nonexistent")
        assert "Error" in result


class TestGlobFiles:
    def test_glob_pattern(self, deps):
        deps.filesystem.mkdir(deps.repo_path / "src")
        deps.filesystem.write_text(deps.repo_path / "src" / "main.py", "code")
        deps.filesystem.write_text(deps.repo_path / "readme.md", "docs")
        result = glob_files(_ctx(deps), "*.md")
        assert isinstance(result, list)
        assert "readme.md" in result

    def test_glob_nested_pattern(self, deps):
        deps.filesystem.mkdir(deps.repo_path / "src")
        deps.filesystem.write_text(deps.repo_path / "src" / "main.py", "code")
        result = glob_files(_ctx(deps), "src/main.py")
        assert "src/main.py" in result

    def test_absolute_pattern_escape(self, deps):
        result = glob_files(_ctx(deps), "/etc/passwd")
        assert any("Error" in r or "escapes" in r for r in result)


class TestReadFile:
    def test_read_lines(self, deps):
        deps.filesystem.write_text(deps.repo_path / "test.txt", "line1\nline2\nline3\n")
        result = read_file(_ctx(deps), "test.txt", 1, 2)
        assert "line1" in result
        assert "line2" in result
        assert "line3" not in result

    def test_read_nonexistent(self, deps):
        result = read_file(_ctx(deps), "missing.txt", 1, 10)
        assert "Error" in result


class TestGrep:
    def test_grep_finds_nothing(self, deps):
        deps.filesystem.write_text(deps.repo_path / "test.txt", "hello world\n")
        result = grep(_ctx(deps), "nonexistent", "test.txt")
        assert result == []

    def test_grep_single_file(self, deps):
        deps.filesystem.write_text(deps.repo_path / "test.txt", "hello world\nfoo bar\n")
        result = grep(_ctx(deps), "hello", "test.txt")
        assert len(result) == 1
        assert "test.txt:1:hello world" in result[0]

    def test_grep_recursive(self, deps):
        deps.filesystem.mkdir(deps.repo_path / "src")
        deps.filesystem.write_text(deps.repo_path / "src" / "a.py", "import os\nfoo = 1\n")
        deps.filesystem.write_text(deps.repo_path / "src" / "b.py", "import sys\nbar = 2\n")
        result = grep(_ctx(deps), "import", ".")
        assert len(result) == 2
        assert any("a.py" in r for r in result)
        assert any("b.py" in r for r in result)


class TestWriteFile:
    def test_write_new_file(self, deps):
        result = write_file(_ctx(deps), "output.txt", "content here")
        assert "Wrote" in result
        assert deps.filesystem.read_text(deps.repo_path / "output.txt") == "content here"

    def test_write_creates_parents(self, deps):
        result = write_file(_ctx(deps), "deep/nested/file.txt", "data")
        assert "Wrote" in result

    def test_write_directory_error(self, deps):
        deps.filesystem.mkdir(deps.repo_path / "mydir")
        result = write_file(_ctx(deps), "mydir", "content")
        assert "Error" in result


class TestStrReplace:
    def test_replace_unique(self, deps):
        deps.filesystem.write_text(deps.repo_path / "file.py", "old_value = 1\n")
        result = str_replace(_ctx(deps), "file.py", "old_value", "new_value")
        assert "Edited" in result
        content = deps.filesystem.read_text(deps.repo_path / "file.py")
        assert "new_value" in content

    def test_replace_not_found(self, deps):
        deps.filesystem.write_text(deps.repo_path / "file.py", "content")
        result = str_replace(_ctx(deps), "file.py", "missing", "new")
        assert "not found" in result

    def test_replace_ambiguous(self, deps):
        deps.filesystem.write_text(deps.repo_path / "file.py", "x = 1\nx = 2\n")
        result = str_replace(_ctx(deps), "file.py", "x = ", "y = ")
        assert "appears 2 times" in result


class TestBash:
    def test_success(self, deps):
        result = bash(_ctx(deps), "echo hello")
        assert result["success"] is True
        assert "hello" in result["stdout"]

    def test_failure(self, deps):
        result = bash(_ctx(deps), "exit 1")
        assert result["success"] is False
        assert result["exit_code"] == 1

    def test_timeout(self, deps):
        result = bash(_ctx(deps), "sleep 10", timeout=1)
        assert result["success"] is False
        assert "timed out" in result["stderr"]

    def test_dangerous_command(self, deps):
        result = bash(_ctx(deps), "rm -rf /")
        assert result["success"] is False

    def test_output_spill(self, deps):
        # Generate large output
        large = "x" * (OUTPUT_SPILL_THRESHOLD + 100)
        result = bash(_ctx(deps), f"echo '{large}'")
        if result["success"]:
            assert "too large" in result["stdout"] or len(result["stdout"]) <= OUTPUT_SPILL_THRESHOLD + 200


class TestBuildTools:
    def test_default_tools(self):
        config = Config(agent=AgentConfig(provider="openai", model="gpt-4o"))
        tools = build_tools(config)
        assert len(tools) == 7


class TestPathEscape:
    def test_resolve_path_escape(self, deps):
        ctx = _ctx(deps)
        result = write_file(ctx, "../../../etc/passwd", "evil")
        assert "Error" in result
