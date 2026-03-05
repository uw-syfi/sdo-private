"""Property-based tests for filesystem operations using hypothesis.

These tests use hypothesis to generate random inputs and test filesystem
behavior with a wide variety of edge cases that manual tests might miss.

Note: These tests require hypothesis to be installed. Install with:
    uv add --dev hypothesis
"""

from pathlib import Path

import pytest

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    # Create dummy decorators and classes for when hypothesis is not available

    def given(*args, **kwargs):
        return pytest.mark.skip(reason="hypothesis not installed")

    def assume(*args, **kwargs):
        pass

    class DummySettings:
        def __call__(self, *args, **kwargs):
            return pytest.mark.skip(reason="hypothesis not installed")

    class _DummyStrategy:
        """Placeholder that supports arbitrary chaining and operators."""

        def __getattr__(self, name):
            return lambda *args, **kwargs: self

        def __or__(self, other):
            return self

    class DummyStrategies:
        def __getattr__(self, name):
            return lambda *args, **kwargs: _DummyStrategy()

    settings = DummySettings()
    st = DummyStrategies()

from app_operator.filesystem import InMemoryFilesystem

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed - install with: uv add --dev hypothesis"
)

# Custom strategies for filesystem testing
if HYPOTHESIS_AVAILABLE:

    @st.composite
    def valid_filename(draw):
        """Generate valid filenames (no path separators)."""
        # Generate strings that don't contain path separators
        chars = st.characters(
            blacklist_categories=("Cs", "Cc"),  # No surrogates or control chars
            blacklist_characters="/\\\x00",  # No path separators or null
        )
        name = draw(st.text(chars, min_size=1, max_size=100))
        # Filter out "." and ".." which are special
        assume(name not in (".", ".."))
        return name

    @st.composite
    def valid_path_component(draw):
        """Generate valid path components."""
        # Similar to filename but allow some special cases
        chars = st.characters(
            blacklist_categories=("Cs", "Cc"),
            blacklist_characters="/\\\x00",
        )
        component = draw(st.text(chars, min_size=1, max_size=50))
        assume(component not in (".", ".."))
        return component

    @st.composite
    def relative_path_strategy(draw):
        """Generate relative paths."""
        num_components = draw(st.integers(min_value=1, max_value=5))
        components = [draw(valid_path_component()) for _ in range(num_components)]
        return Path(*components)

    @st.composite
    def absolute_path_strategy(draw):
        """Generate absolute paths."""
        num_components = draw(st.integers(min_value=1, max_value=5))
        components = [draw(valid_path_component()) for _ in range(num_components)]
        return Path("/") / Path(*components)

    @st.composite
    def file_content_strategy(draw):
        """Generate various file contents."""
        return draw(
            st.one_of(
                st.text(min_size=0, max_size=10000),  # Regular text
                st.binary(min_size=0, max_size=10000).map(lambda b: b.decode("utf-8", errors="ignore")),
                # Binary-like
                st.text(st.characters(min_codepoint=0x1F300, max_codepoint=0x1F6FF)),  # Emoji
            )
        )
else:
    # Dummy strategies for when hypothesis is not available
    def valid_filename():
        pass

    def valid_path_component():
        pass

    def relative_path_strategy():
        pass

    def absolute_path_strategy():
        pass

    def file_content_strategy():
        pass


class TestPropertyBasedFilesystem:
    """Property-based tests for filesystem operations."""

    @given(path=relative_path_strategy(), content=file_content_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_write_then_read_preserves_content(self, path, content):
        """Test that writing and reading a file preserves content."""
        fs = InMemoryFilesystem()

        # Create parent directories
        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        # Write content
        fs.write_text(path, content)

        # Read should return same content
        assert fs.read_text(path) == content

    @given(path=absolute_path_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_mkdir_then_exists_is_true(self, path):
        """Test that creating a directory makes it exist."""
        fs = InMemoryFilesystem()

        # Create directory
        fs.mkdir(path, parents=True)

        # Should exist
        assert fs.exists(path)

    @given(
        path=relative_path_strategy(),
        content1=file_content_strategy(),
        content2=file_content_strategy(),
    )
    @settings(max_examples=50, deadline=1000)
    def test_overwrite_replaces_content(self, path, content1, content2):
        """Test that overwriting a file replaces its content."""
        fs = InMemoryFilesystem()

        # Create parent if needed
        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        # Write first content
        fs.write_text(path, content1)

        # Overwrite with second content
        fs.write_text(path, content2)

        # Should have second content
        assert fs.read_text(path) == content2

    @given(path=relative_path_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_remove_makes_not_exist(self, path):
        """Test that removing a file makes it not exist."""
        fs = InMemoryFilesystem()

        # Create parent if needed
        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        # Create file
        fs.write_text(path, "content")
        assert fs.exists(path)

        # Remove file
        fs.remove(path)

        # Should not exist
        assert not fs.exists(path)

    @given(
        paths=st.lists(relative_path_strategy(), min_size=1, max_size=10, unique=True),
        content=file_content_strategy(),
    )
    @settings(max_examples=30, deadline=1000)
    def test_multiple_files_independent(self, paths, content):
        """Test that multiple files can coexist independently."""
        # Filter paths to ensure no path is a prefix of another to avoid conflicts
        # e.g., 'a' and 'a/b' cannot both be files
        paths.sort(key=lambda p: len(str(p)))
        filtered_paths = []
        for i, p in enumerate(paths):
            is_prefix = False
            for other in paths[i + 1 :]:
                if str(other).startswith(str(p) + "/"):
                    is_prefix = True
                    break
            if not is_prefix:
                filtered_paths.append(p)

        paths = filtered_paths

        fs = InMemoryFilesystem()

        # Create all files
        for path in paths:
            if path.parent != Path("."):
                fs.mkdir(path.parent, parents=True)
            fs.write_text(path, content)

        # All should exist
        for path in paths:
            assert fs.exists(path)
            assert fs.read_text(path) == content

    @given(
        path=relative_path_strategy(),
        perms=st.integers(min_value=0, max_value=0o777),
    )
    @settings(max_examples=50, deadline=1000)
    def test_chmod_sets_permissions(self, path, perms):
        """Test that chmod sets permissions correctly."""
        fs = InMemoryFilesystem()

        # Create file
        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)
        fs.write_text(path, "content")

        # Set permissions
        fs.chmod(path, perms)

        # Permissions should be set
        assert fs.permissions.get(fs._normalize_path(path)) == perms

    @given(filename=valid_filename())
    @settings(max_examples=50, deadline=1000)
    def test_simple_filename_operations(self, filename):
        """Test operations with simple filenames (no paths)."""
        fs = InMemoryFilesystem()
        path = Path(filename)

        # Write
        fs.write_text(path, "test")

        # Should exist
        assert fs.exists(path)

        # Read
        assert fs.read_text(path) == "test"

        # Remove
        fs.remove(path)

        # Should not exist
        assert not fs.exists(path)

    @given(
        base_path=relative_path_strategy(),
        subdirs=st.lists(valid_path_component(), min_size=1, max_size=5),
    )
    @settings(max_examples=30, deadline=1000)
    def test_nested_directory_creation(self, base_path, subdirs):
        """Test creating nested directory structures."""
        fs = InMemoryFilesystem()

        # Build nested path
        full_path = base_path
        for subdir in subdirs:
            full_path = full_path / subdir

        # Create all at once
        fs.mkdir(full_path, parents=True)

        # Should exist
        assert fs.exists(full_path)

        # All parent dirs should exist
        current = full_path
        while current != Path("."):
            assert fs.exists(current)
            current = current.parent
            if current == Path("."):
                break

    @given(
        paths=st.lists(
            st.tuples(relative_path_strategy(), file_content_strategy()),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=30, deadline=1000)
    def test_batch_operations(self, paths):
        """Test batch file operations."""
        fs = InMemoryFilesystem()

        # Filter to unique paths
        unique_paths_map = {}
        for path, content in paths:
            unique_paths_map[str(path)] = (path, content)

        # Sort paths by length to make prefix checking easier
        sorted_paths = sorted(unique_paths_map.values(), key=lambda x: len(str(x[0])))

        filtered_paths = []
        for i, (path, content) in enumerate(sorted_paths):
            is_prefix = False
            for other_path, _ in sorted_paths[i + 1 :]:
                if str(other_path).startswith(str(path) + "/"):
                    is_prefix = True
                    break
            if not is_prefix:
                filtered_paths.append((path, content))

        # Create all files
        for path, content in filtered_paths:
            if path.parent != Path("."):
                fs.mkdir(path.parent, parents=True)
            fs.write_text(path, content)

        # Verify all exist with correct content
        for path, content in filtered_paths:
            assert fs.exists(path)
            assert fs.read_text(path) == content


class TestPropertyBasedEdgeCases:
    """Property-based tests for edge cases."""

    @given(content=st.text(min_size=0, max_size=1))
    @settings(max_examples=50, deadline=1000)
    def test_very_short_content(self, content):
        """Test with very short content including empty string."""
        fs = InMemoryFilesystem()
        path = Path("test.txt")

        fs.write_text(path, content)
        assert fs.read_text(path) == content

    @given(
        path=relative_path_strategy(),
        content=st.text(min_size=1000, max_size=4000),
    )
    @settings(max_examples=10, deadline=2000)
    def test_large_content(self, path, content):
        """Test with large file content."""
        fs = InMemoryFilesystem()

        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        fs.write_text(path, content)
        assert fs.read_text(path) == content

    @given(
        path=relative_path_strategy(),
        content=st.text(st.characters(whitelist_categories=("Zs", "Zl", "Zp"))),
    )
    @settings(max_examples=30, deadline=1000)
    def test_whitespace_only_content(self, path, content):
        """Test with whitespace-only content."""
        fs = InMemoryFilesystem()

        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        fs.write_text(path, content)
        assert fs.read_text(path) == content

    @given(
        operations=st.lists(
            st.sampled_from(["write", "read", "remove", "exists"]),
            min_size=5,
            max_size=20,
        )
    )
    @settings(max_examples=30, deadline=1000)
    def test_random_operation_sequence(self, operations):
        """Test random sequences of filesystem operations."""
        fs = InMemoryFilesystem()
        path = Path("test.txt")

        file_exists = False

        for op in operations:
            if op == "write":
                fs.write_text(path, "content")
                file_exists = True
            elif op == "read":
                if file_exists:
                    assert fs.read_text(path) == "content"
            elif op == "remove":
                if file_exists:
                    fs.remove(path)
                    file_exists = False
            elif op == "exists":
                assert fs.exists(path) == file_exists

    @given(
        path=relative_path_strategy(),
        num_writes=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=20, deadline=2000)
    def test_repeated_overwrites(self, path, num_writes):
        """Test many repeated overwrites of the same file."""
        fs = InMemoryFilesystem()

        if path.parent != Path("."):
            fs.mkdir(path.parent, parents=True)

        # Write many times
        for i in range(num_writes):
            content = f"version {i}"
            fs.write_text(path, content)

        # Should have final version
        assert fs.read_text(path) == f"version {num_writes - 1}"


class TestPropertyBasedConcurrency:
    """Property-based tests for concurrent operations."""

    @given(
        paths=st.lists(relative_path_strategy(), min_size=2, max_size=10, unique=True),
        content=file_content_strategy(),
    )
    @settings(max_examples=20, deadline=2000)
    def test_concurrent_writes_different_files(self, paths, content):
        """Test concurrent writes to different files."""
        # Filter paths to ensure no path is a prefix of another to avoid conflicts
        # e.g., 'a' and 'a/b' cannot both be files
        paths.sort(key=lambda p: len(str(p)))
        filtered_paths = []
        for i, p in enumerate(paths):
            is_prefix = False
            for other in paths[i + 1 :]:
                if str(other).startswith(str(p) + "/"):
                    is_prefix = True
                    break
            if not is_prefix:
                filtered_paths.append(p)

        assume(len(filtered_paths) >= 2)
        paths = filtered_paths

        import threading

        fs = InMemoryFilesystem()
        errors = []

        def write_file(path):
            try:
                if path.parent != Path("."):
                    fs.mkdir(path.parent, parents=True)
                fs.write_text(path, content)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_file, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0

        # All files should exist
        for path in paths:
            assert fs.exists(path)
