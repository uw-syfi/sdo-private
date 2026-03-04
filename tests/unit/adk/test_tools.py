from pathlib import Path
from unittest.mock import MagicMock, patch
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


def test_list_files():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)
    fs.write_text(repo_root / "file1.txt", "content")
    fs.write_text(repo_root / "file2.txt", "content")
    subdir = repo_root / "subdir"
    fs.mkdir(subdir)

    tools = {t.__name__: t for t in build_tools(repo_root, fs)}

    with patch("pathlib.Path.iterdir") as mock_iterdir:
        mock_iterdir.return_value = [
            Path("file1.txt"),
            Path("file2.txt"),
            Path("subdir"),
        ]
        result = tools["list_files"](".")
        assert result["status"] == "success"
        assert "file1.txt" in result["output"]
        assert "file2.txt" in result["output"]
        assert "subdir" in result["output"]

    # Test non-existent path
    result = tools["list_files"]("nonexistent")
    assert result["status"] == "error"
    assert "Path does not exist" in result["error"]


def test_find_files():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = {t.__name__: t for t in build_tools(repo_root, fs)}

    with patch("pathlib.Path.glob") as mock_glob:
        p1 = MagicMock(spec=Path)
        p1.is_file.return_value = True
        p1.relative_to.return_value = Path("src/main.py")
        p1.__str__ = lambda self: str(repo_root / "src/main.py")

        p2 = MagicMock(spec=Path)
        p2.is_file.return_value = True
        p2.relative_to.return_value = Path("src/utils.py")
        p2.__str__ = lambda self: str(repo_root / "src/utils.py")

        mock_glob.return_value = [p1, p2]

        result = tools["find_files"]("**/*.py")
        assert result["status"] == "success"
        assert "src/main.py" in result["output"]
        assert "src/utils.py" in result["output"]


def test_read_file():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)
    file_path = repo_root / "test.txt"
    fs.write_text(file_path, "Hello World")

    tools = {t.__name__: t for t in build_tools(repo_root, fs)}

    result = tools["read_file"]("test.txt")
    assert result["status"] == "success"
    assert result["output"] == "Hello World"

    # Read non-existent
    result = tools["read_file"]("missing.txt")
    assert result["status"] == "error"


def test_search_content():
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)
    file_path = repo_root / "test.log"
    content = "Error: something went wrong\nInfo: all good\nError: another one"
    fs.write_text(file_path, content)

    tools = {t.__name__: t for t in build_tools(repo_root, fs)}

    # Test single file search
    result = tools["search_content"]("Error", "test.log")
    assert result["status"] == "success"
    assert "test.log:1:Error: something went wrong" in result["output"]
    assert "test.log:3:Error: another one" in result["output"]

    # Test recursive search (requires mocking rglob)
    with patch("pathlib.Path.rglob") as mock_rglob:
        p = MagicMock(spec=Path)
        p.is_file.return_value = True
        p.relative_to.return_value = Path("test.log")
        p.__str__ = lambda self: str(file_path)

        mock_rglob.return_value = [p]

        result = tools["search_content"]("Info", ".")
        assert result["status"] == "success"
        assert "test.log:2:Info: all good" in result["output"]


@patch("subprocess.run")
def test_run_command(mock_run):
    fs = InMemoryFilesystem()
    repo_root = Path("/repo")
    fs.mkdir(repo_root)

    tools = {t.__name__: t for t in build_tools(repo_root, fs)}

    # Mock success
    mock_run.return_value = MagicMock(
        returncode=0, stdout="command output", stderr=""
    )

    result = tools["run_command"]("ls -la", 10)
    assert result["status"] == "success"
    assert result["output"] == "command output"

    # Mock failure
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="command failed"
    )

    result = tools["run_command"]("invalid", 10)
    assert result["status"] == "error"
    assert result["error"] == "command failed"
