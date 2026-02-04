import pytest
from pathlib import Path
from app_operator.filesystem import InMemoryFilesystem


@pytest.fixture
def fs():
    return InMemoryFilesystem()


def test_remove_non_existent_file(fs):
    """Test removing a file that does not exist."""
    path = Path("/non/existent/file.txt")
    with pytest.raises(FileNotFoundError, match="No such file"):
        fs.remove(path)


def test_write_text_to_directory(fs):
    """Test writing text to a path that is a directory."""
    path = Path("/some/dir")
    fs.mkdir(path)
    with pytest.raises(IsADirectoryError, match="Is a directory"):
        fs.write_text(path, "content")


def test_mkdir_missing_parents_false(fs):
    """Test mkdir with parents=False when parents missing."""
    path = Path("/foo/bar/baz")
    with pytest.raises(FileNotFoundError, match="Parent directory does not exist"):
        fs.mkdir(path, parents=False)


def test_chmod(fs):
    """Test chmod implementation."""
    file_path = Path("file.txt")
    fs.write_text(file_path, "content")

    fs.chmod(file_path, 0o777)
    assert fs.permissions[str(file_path)] == 0o777


def test_chmod_non_existent(fs):
    """Test chmod on non-existent file."""
    path = Path("non_existent")
    with pytest.raises(FileNotFoundError, match="No such file or directory"):
        fs.chmod(path, 0o777)
