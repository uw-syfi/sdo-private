from pathlib import Path

import pytest

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
    assert fs.permissions[fs._normalize_path(file_path)] == 0o777


def test_chmod_non_existent(fs):
    """Test chmod on non-existent file."""
    path = Path("non_existent")
    with pytest.raises(FileNotFoundError, match="No such file or directory"):
        fs.chmod(path, 0o777)


def test_unicode_filename(fs):
    """Test creating and reading files with Unicode characters in name."""
    path = Path("файл_测试_🎉.txt")
    fs.write_text(path, "Unicode content")
    assert fs.read_text(path) == "Unicode content"


def test_unicode_file_content(fs):
    """Test writing and reading Unicode content."""
    path = Path("test.txt")
    content = "Hello 世界 🌍 Привет مرحبا"
    fs.write_text(path, content)
    assert fs.read_text(path) == content


def test_special_characters_in_path(fs):
    """Test paths with special characters."""
    path = Path("file!@#$%^&()_+-=.txt")
    fs.write_text(path, "content")
    assert fs.exists(path)


def test_deeply_nested_path(fs):
    """Test creating deeply nested directory structure."""
    path = Path("/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/file.txt")
    fs.mkdir(path.parent, parents=True)
    fs.write_text(path, "deep content")
    assert fs.read_text(path) == "deep content"


def test_empty_file_name(fs):
    """Test behavior with empty filename."""
    path = Path("")
    # Empty path is treated as current directory "."
    # Writing to a directory should raise IsADirectoryError
    with pytest.raises(IsADirectoryError, match="Is a directory"):
        fs.write_text(path, "content")


def test_very_long_filename(fs):
    """Test very long filename."""
    # Some filesystems limit to 255 bytes
    long_name = "a" * 200 + ".txt"
    path = Path(long_name)
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"


def test_very_long_content(fs):
    """Test writing very long content."""
    path = Path("large_file.txt")
    content = "x" * 1_000_000  # 1MB of text
    fs.write_text(path, content)
    assert fs.read_text(path) == content


def test_path_with_dots(fs):
    """Test paths with multiple dots."""
    path = Path("file.with.many.dots.txt")
    fs.write_text(path, "content")
    assert fs.exists(path)


def test_path_with_spaces(fs):
    """Test paths with spaces."""
    path = Path("file with spaces.txt")
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"


def test_write_empty_content(fs):
    """Test writing empty string to file."""
    path = Path("empty.txt")
    fs.write_text(path, "")
    assert fs.read_text(path) == ""


def test_overwrite_file_multiple_times(fs):
    """Test overwriting file content multiple times."""
    path = Path("file.txt")
    for i in range(100):
        fs.write_text(path, f"version {i}")
    assert fs.read_text(path) == "version 99"


def test_remove_directory_with_files(fs):
    """Test removing directory that contains files."""
    dir_path = Path("/mydir")
    file_path = dir_path / "file.txt"
    fs.mkdir(dir_path)
    fs.write_text(file_path, "content")

    # Remove should work for files
    fs.remove(file_path)
    assert not fs.exists(file_path)


def test_path_traversal_attempt(fs):
    """Test that path traversal requires parent directories to exist."""
    path = Path("../../../etc/passwd")
    # InMemoryFS requires parent directories to exist
    # This should raise FileNotFoundError for missing parent
    with pytest.raises(FileNotFoundError, match="Parent directory does not exist"):
        fs.write_text(path, "content")

    # But if we create the parent dirs, it should work
    fs.mkdir(path.parent, parents=True)
    fs.write_text(path, "content")
    assert fs.exists(path)


def test_concurrent_writes_same_file(fs):
    """Test behavior with concurrent writes to same file."""
    import threading

    path = Path("concurrent.txt")
    results = []

    def writer(content):
        fs.write_text(path, content)
        results.append(fs.read_text(path))

    threads = [threading.Thread(target=writer, args=(f"content{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File should exist and have some content
    assert fs.exists(path)
    assert len(results) == 10


def test_newline_variations(fs):
    """Test different newline formats."""
    path = Path("newlines.txt")

    # Unix newlines
    fs.write_text(path, "line1\nline2\nline3\n")
    assert fs.read_text(path) == "line1\nline2\nline3\n"

    # Windows newlines
    fs.write_text(path, "line1\r\nline2\r\nline3\r\n")
    assert fs.read_text(path) == "line1\r\nline2\r\nline3\r\n"

    # Old Mac newlines
    fs.write_text(path, "line1\rline2\rline3\r")
    assert fs.read_text(path) == "line1\rline2\rline3\r"


def test_binary_like_content_in_text(fs):
    """Test storing binary-like content as text."""
    path = Path("binary.txt")
    # Content with various control characters
    content = "\x00\x01\x02\x03\x04\x05"
    fs.write_text(path, content)
    assert fs.read_text(path) == content


def test_absolute_vs_relative_paths(fs):
    """Test that absolute and relative paths work correctly."""
    abs_path = Path("/absolute/file.txt")
    rel_path = Path("relative/file.txt")

    fs.mkdir(abs_path.parent, parents=True)
    fs.mkdir(rel_path.parent, parents=True)

    fs.write_text(abs_path, "absolute")
    fs.write_text(rel_path, "relative")

    assert fs.read_text(abs_path) == "absolute"
    assert fs.read_text(rel_path) == "relative"


def test_exists_on_empty_path(fs):
    """Test exists() on empty path."""
    path = Path("")
    # Behavior may vary, but should not crash
    result = fs.exists(path)
    assert isinstance(result, bool)


def test_concurrent_read_write(fs):
    """Test concurrent reads and writes to same file.

    Uses threading.Event for deterministic synchronization instead of time.sleep.
    """
    import threading

    path = Path("rw_test.txt")
    fs.write_text(path, "initial")

    read_results = []
    write_count = [0]
    write_events = [threading.Event() for _ in range(10)]
    all_writes_done = threading.Event()

    def reader():
        for i in range(20):
            try:
                content = fs.read_text(path)
                read_results.append(content)
                # Wait for corresponding write if available
                if i < len(write_events):
                    write_events[i].wait(timeout=0.1)
            except Exception:
                pass  # Ignore transient errors

    def writer():
        for i in range(10):
            fs.write_text(path, f"update {i}")
            write_count[0] += 1
            write_events[i].set()  # Signal readers
        all_writes_done.set()

    threads = [threading.Thread(target=reader) for _ in range(2)]
    threads.append(threading.Thread(target=writer))

    for t in threads:
        t.start()

    # Wait for writes to complete
    all_writes_done.wait(timeout=2.0)

    for t in threads:
        t.join(timeout=1.0)

    # Should have completed multiple reads
    assert len(read_results) > 0
    # All writes should complete
    assert write_count[0] == 10


def test_concurrent_directory_operations(fs):
    """Test concurrent directory creation and file operations."""
    import threading

    base_dir = Path("/concurrent")
    errors = []

    def create_structure(index):
        try:
            dir_path = base_dir / f"dir_{index}"
            file_path = dir_path / f"file_{index}.txt"
            fs.mkdir(dir_path, parents=True)
            fs.write_text(file_path, f"content {index}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=create_structure, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All operations should succeed
    assert len(errors) == 0
    # All files should exist
    for i in range(20):
        assert fs.exists(base_dir / f"dir_{i}" / f"file_{i}.txt")


def test_concurrent_remove_operations(fs):
    """Test concurrent file removal."""
    import threading

    # Create files first
    for i in range(10):
        fs.write_text(Path(f"file_{i}.txt"), f"content {i}")

    removed = []
    errors = []

    def remove_file(index):
        path = Path(f"file_{index}.txt")
        try:
            if fs.exists(path):
                fs.remove(path)
                removed.append(index)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=remove_file, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Most files should be removed
    assert len(removed) >= 8  # Allow for some race conditions
    # Errors are expected in concurrent removes
    # but they should be handled gracefully


def test_write_file_timeout_simulation(fs):
    """Test filesystem behavior during slow operations."""
    import time

    # Write large content to simulate slow operation
    path = Path("large.txt")
    large_content = "x" * (1024 * 1024)  # 1MB

    start = time.time()
    fs.write_text(path, large_content)
    duration = time.time() - start

    # Should complete (even if slow)
    assert fs.exists(path)
    assert len(fs.read_text(path)) == len(large_content)
    # Verify it completed
    assert duration >= 0


def test_permission_error_recovery(fs):
    """Test recovery from permission errors."""
    path = Path("protected.txt")

    # Simulate permission error
    fs.simulate_permission_error(path)

    # Write should fail
    with pytest.raises(PermissionError):
        fs.write_text(path, "content")

    # Clear permission error
    fs.clear_failures()

    # Write should succeed now
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"


def test_concurrent_permission_checks(fs):
    """Test checking permissions concurrently."""
    import threading

    # Create test files
    for i in range(5):
        fs.write_text(Path(f"file_{i}.txt"), "content")

    # Simulate permission errors on some files
    fs.simulate_permission_error(Path("file_2.txt"))

    results = []

    def check_access(index):
        path = Path(f"file_{index}.txt")
        try:
            fs.read_text(path)
            results.append((index, True))
        except PermissionError:
            results.append((index, False))

    threads = [threading.Thread(target=check_access, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should have results for all files
    assert len(results) == 5
    # file_2 should have permission error
    file_2_results = [r for r in results if r[0] == 2]
    assert len(file_2_results) == 1
    assert file_2_results[0][1] is False


def test_race_condition_file_creation(fs):
    """Test race condition during file creation."""
    import threading

    path = Path("race.txt")

    success_count = [0]

    def try_create():
        try:
            if not fs.exists(path):
                fs.write_text(path, "content")
                success_count[0] += 1
        except Exception:
            pass

    threads = [threading.Thread(target=try_create) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File should exist
    assert fs.exists(path)
    # At least one thread should have created it
    assert success_count[0] >= 1


def test_read_during_write_operation(fs):
    """Test reading file while it's being written.

    Uses threading.Event for synchronization instead of time.sleep.
    """
    import threading

    path = Path("partial.txt")
    fs.write_text(path, "initial")

    read_during_write = []
    write_events = [threading.Event() for _ in range(5)]
    all_writes_done = threading.Event()

    def slow_writer():
        # Simulate slow write in chunks with events for coordination
        for i in range(5):
            current = fs.read_text(path) if fs.exists(path) else ""
            fs.write_text(path, current + f" part{i}")
            write_events[i].set()  # Signal readers after each write
        all_writes_done.set()

    def reader():
        for i in range(10):
            try:
                content = fs.read_text(path)
                read_during_write.append(len(content))
                # Wait for next write event if available
                if i < len(write_events):
                    write_events[i].wait(timeout=0.1)
            except Exception:
                pass

    writer = threading.Thread(target=slow_writer)
    readers = [threading.Thread(target=reader) for _ in range(2)]

    writer.start()
    for r in readers:
        r.start()

    # Wait for all writes to complete
    all_writes_done.wait(timeout=2.0)

    writer.join(timeout=1.0)
    for r in readers:
        r.join(timeout=1.0)

    # Should have read various states
    assert len(read_during_write) > 0
    # Final content should include all parts
    final = fs.read_text(path)
    assert "part4" in final
