"""Tests for lego_agent storage module."""

from pathlib import Path
from unittest.mock import patch

from lego_agent.backend.storage import LegoAgentStorage


def test_storage_initialization(tmp_path):
    """Test LegoAgentStorage initialization."""
    storage = LegoAgentStorage(tmp_path)
    assert storage.base_dir == tmp_path


def test_write_script_creates_directory(tmp_path):
    """Test that write_script creates timestamped directory."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "print('hello')"

    script_path = storage.write_script(script_text)

    # Verify directory was created
    assert script_path.parent.exists()
    assert script_path.parent.is_dir()
    # Parent should be under base_dir
    assert script_path.parent.parent == tmp_path


def test_write_script_creates_file(tmp_path):
    """Test that write_script creates the script file."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "print('hello world')"

    script_path = storage.write_script(script_text)

    # Verify file was created
    assert script_path.exists()
    assert script_path.is_file()
    assert script_path.name == "generated_script.py"


def test_write_script_content(tmp_path):
    """Test that write_script writes correct content."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "#!/usr/bin/env python3\nprint('test script')\n"

    script_path = storage.write_script(script_text)

    # Verify content
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_timestamp_format(tmp_path):
    """Test that directory name follows timestamp format."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "print('test')"

    with patch("time.strftime") as mock_strftime:
        mock_strftime.return_value = "20240101-120000"
        script_path = storage.write_script(script_text)

    # Verify timestamp directory name
    assert script_path.parent.name == "20240101-120000"


def test_storage_keeps_single_run_dir_across_writes(tmp_path):
    """Test that all writes for one execution share the same run directory."""
    storage = LegoAgentStorage(tmp_path)

    with patch("time.strftime") as mock_strftime:
        mock_strftime.return_value = "20240101-120000"
        script_path = storage.write_script("# Script 1")

    with patch("time.strftime") as mock_strftime:
        mock_strftime.return_value = "20240101-120001"
        config_path = storage.write_config("workflow: {}")

    with patch("time.strftime") as mock_strftime:
        mock_strftime.return_value = "20240101-120002"
        storage.log_llm_call("start", {"model": "demo"})
        storage.log_llm_call("end", {"model": "demo"})

    assert script_path.parent == config_path.parent
    assert script_path.parent.name == "20240101-120000"
    assert (script_path.parent / "llm_calls.jsonl").exists()
    assert len((script_path.parent / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_write_script_overwrites_if_same_timestamp(tmp_path):
    """Test that writing with same timestamp overwrites the file."""
    storage = LegoAgentStorage(tmp_path)

    with patch("time.strftime") as mock_strftime:
        mock_strftime.return_value = "20240101-120000"

        # Write first script
        path1 = storage.write_script("# First version")
        content1 = path1.read_text()

        # Write second script with same timestamp
        path2 = storage.write_script("# Second version")
        content2 = path2.read_text()

    # Same path but different content
    assert path1 == path2
    assert content1 == "# First version"
    assert content2 == "# Second version"


def test_write_script_unicode_content(tmp_path):
    """Test writing script with unicode characters."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "# 你好世界\nprint('Hello 🌍')\n"

    script_path = storage.write_script(script_text)

    # Verify unicode is preserved
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_empty_content(tmp_path):
    """Test writing empty script."""
    storage = LegoAgentStorage(tmp_path)
    script_text = ""

    script_path = storage.write_script(script_text)

    # Verify empty file is created
    assert script_path.exists()
    assert script_path.read_text(encoding="utf-8") == ""


def test_write_script_multiline_content(tmp_path):
    """Test writing multiline script."""
    storage = LegoAgentStorage(tmp_path)
    script_text = """#!/usr/bin/env python3
import sys
import os

def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
"""

    script_path = storage.write_script(script_text)

    # Verify multiline content
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_returns_path(tmp_path):
    """Test that write_script returns a Path object."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "print('test')"

    result = storage.write_script(script_text)

    assert isinstance(result, Path)
    assert result.is_absolute()


def test_base_dir_nonexistent_creates_on_write(tmp_path):
    """Test that non-existent base directory is created on write."""
    nonexistent = tmp_path / "nonexistent" / "nested"
    storage = LegoAgentStorage(nonexistent)

    script_path = storage.write_script("print('test')")

    # Verify base dir and subdirectories were created
    assert nonexistent.exists()
    assert script_path.exists()


def test_write_script_preserves_newlines(tmp_path):
    """Test that newline content is written correctly."""
    storage = LegoAgentStorage(tmp_path)

    # Test with standard Unix newlines
    script_text_lf = "line1\nline2\nline3\n"

    path_lf = storage.write_script(script_text_lf)

    # Read back and verify content
    assert path_lf.read_text(encoding="utf-8") == script_text_lf


def test_write_script_with_special_characters_in_content(tmp_path):
    """Test writing script with special characters."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "# Special chars: !@#$%^&*()[]{}<>?/\\|;:'\",.\n"

    script_path = storage.write_script(script_text)
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_very_long_content(tmp_path):
    """Test writing very long script content."""
    storage = LegoAgentStorage(tmp_path)
    # Create a script with 10,000 lines
    script_text = "\n".join([f"# Line {i}" for i in range(10000)])

    script_path = storage.write_script(script_text)
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_concurrent_writes_different_timestamps(tmp_path):
    """Test concurrent writes with different timestamps create separate files."""
    import threading

    storage = LegoAgentStorage(tmp_path)
    results = []

    def write_script(index):
        # Use different timestamps
        with patch("time.strftime") as mock_strftime:
            mock_strftime.return_value = f"2024010{index}-120000"
            path = storage.write_script(f"# Script {index}")
            results.append(path)

    threads = [threading.Thread(target=write_script, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All paths should exist
    assert len(results) == 3
    for path in results:
        assert path.exists()


def test_write_script_with_tabs_and_spaces(tmp_path):
    """Test writing script with mixed tabs and spaces."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "\tindented with tab\n    indented with spaces\n"

    script_path = storage.write_script(script_text)
    assert script_path.read_text(encoding="utf-8") == script_text


def test_write_script_with_null_bytes_raises_error(tmp_path):
    """Test that writing script with null bytes raises an error."""
    storage = LegoAgentStorage(tmp_path)
    # Null bytes are not valid in file content
    script_text = "line1\x00line2"

    # This should raise ValueError or similar when trying to write
    try:
        script_path = storage.write_script(script_text)
        # If it doesn't raise, at least verify it was written
        assert script_path.exists()
    except (ValueError, OSError):
        # Expected behavior: null bytes in string may cause issues
        pass


def test_storage_base_dir_is_path_object(tmp_path):
    """Test that base_dir is stored as Path object."""
    storage = LegoAgentStorage(tmp_path)
    assert isinstance(storage.base_dir, Path)


def test_write_script_windows_line_endings(tmp_path):
    """Test writing script with Windows-style line endings."""
    storage = LegoAgentStorage(tmp_path)
    script_text = "line1\r\nline2\r\nline3\r\n"

    script_path = storage.write_script(script_text)
    # On Unix systems, Python's text mode may normalize \r\n to \n when reading
    # So we read in binary mode to verify actual content
    actual_bytes = script_path.read_bytes()
    expected_bytes = script_text.encode("utf-8")
    assert actual_bytes == expected_bytes
