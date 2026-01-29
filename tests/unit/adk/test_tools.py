from pathlib import Path
from app_operator.filesystem import InMemoryFilesystem
from app_operator.adk.tools import build_tools


def test_resolve_path_prevents_escape():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)
    read_tool = next(t for t in tools if t.__name__ == "read_file")

    # Test path escaping
    result = read_tool("../secret.txt")
    assert result["status"] == "error"
    assert "escapes repository root" in result["error"]


def test_write_file_creates_file():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)
    write_tool = next(t for t in tools if t.__name__ == "write_file")

    # Write file
    result = write_tool("test.txt", "hello world")
    assert result["status"] == "success"
    assert "Wrote" in result["output"]

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
    assert result["status"] == "success"
    assert "Wrote" in result["output"]

    # Verify content
    assert fs.read_text(repo_root / "nested/dir/test.txt") == "hello nested"


def test_tools_scope_restriction():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = build_tools(repo_root, fs)

    # Identify tools
    list_files = next(t for t in tools if t.__name__ == "list_files")
    find_files = next(t for t in tools if t.__name__ == "find_files")
    read_file = next(t for t in tools if t.__name__ == "read_file")
    search_content = next(t for t in tools if t.__name__ == "search_content")

    # 1. Test list_files (ls) escape
    result = list_files("../")
    assert result["status"] == "error"
    assert "escapes repository root" in result["error"]

    # 2. Test read_file (read) escape
    result = read_file("../secret.txt")
    assert result["status"] == "error"
    assert "escapes repository root" in result["error"]

    # 3. Test search_content (grep) escape
    result = search_content("pattern", "../")
    assert result["status"] == "error"
    assert "escapes repository root" in result["error"]

    # 4. Test find_files (glob) escape - absolute path outside
    result = find_files("/etc/passwd")
    assert result["status"] == "error"
    assert "escapes repository root" in result["error"]
