from pathlib import Path
from app_operator.filesystem import InMemoryFilesystem
from app_operator.adk.tools import build_tools


def test_resolve_path_prevents_escape():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)
    read_tool = next(t for t in tools if t.__name__ == "read")

    # Test path escaping
    result = read_tool("../secret.txt")
    assert "Error" in result
    assert "escapes repository root" in result


def test_write_file_creates_file():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)
    write_tool = next(t for t in tools if t.__name__ == "write_file")

    # Write file
    result = write_tool("test.txt", "hello world")
    assert "Wrote" in result

    # Verify content
    assert fs.read_text(repo_root / "test.txt") == "hello world"


def test_write_file_creates_parent_dirs():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)
    write_tool = next(t for t in tools if t.__name__ == "write_file")

    # Write file in subdir
    result = write_tool("nested/dir/test.txt", "hello nested")
    assert "Wrote" in result

    # Verify content
    assert fs.read_text(repo_root / "nested/dir/test.txt") == "hello nested"
