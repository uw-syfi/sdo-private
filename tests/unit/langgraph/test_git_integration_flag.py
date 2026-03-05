from pathlib import Path

from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.tools import build_tools


def test_git_tool_absent_by_default():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)
    tools = build_tools(repo_root, fs)
    names = [t.name for t in tools]
    assert "make_change_on_remote_copy" not in names


def test_git_tool_present_when_enabled():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)
    tools = build_tools(repo_root, fs, git_integration=True)
    names = [t.name for t in tools]
    assert "make_change_on_remote_copy" in names
