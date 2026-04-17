from pathlib import Path
from unittest.mock import MagicMock

from app_operator.core import RealFilesystem


def test_real_filesystem_wrappers():
    fs = RealFilesystem()
    mock_path = MagicMock(spec=Path)

    # Test exists
    fs.exists(mock_path)
    mock_path.exists.assert_called_once()

    # Test is_dir
    fs.is_dir(mock_path)
    mock_path.is_dir.assert_called_once()

    # Test mkdir
    fs.mkdir(mock_path, parents=True, exist_ok=True)
    mock_path.mkdir.assert_called_with(parents=True, exist_ok=True)

    # Test write_text
    fs.write_text(mock_path, "content", encoding="utf-8")
    mock_path.write_text.assert_called_with("content", encoding="utf-8")

    # Test read_text
    fs.read_text(mock_path, encoding="utf-8")
    mock_path.read_text.assert_called_with(encoding="utf-8")

    # Test chmod
    fs.chmod(mock_path, 0o755)
    mock_path.chmod.assert_called_with(0o755)

    # Test remove
    fs.remove(mock_path)
    mock_path.unlink.assert_called_once()
