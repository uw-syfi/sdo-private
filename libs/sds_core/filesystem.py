"""Filesystem abstraction layer for testability.

This module provides an abstraction over filesystem operations to enable
dependency injection and testing without actual file I/O.
"""

import fnmatch
import posixpath
import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class FileSystemInterface(ABC):
    """Abstract interface for filesystem operations."""

    @abstractmethod
    def exists(self, path: Path) -> bool:
        """Check if a path exists.

        Args:
            path: The path to check.

        Returns:
            bool: True if the path exists, False otherwise.
        """

    @abstractmethod
    def is_dir(self, path: Path) -> bool:
        """Check if a path is a directory.

        Args:
            path: The path to check.

        Returns:
            bool: True if the path is a directory, False otherwise.
        """

    @abstractmethod
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None:
        """Create a directory.

        Args:
            path: The directory path to create.
            parents: If True, create parent directories as needed.
            exist_ok: If True, don't raise an error if the directory exists.
        """

    @abstractmethod
    def write_text(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        """Write text to a file.

        Args:
            path: The file path to write to.
            content: The text content to write.
            encoding: The text encoding to use.
        """

    @abstractmethod
    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        """Read text from a file.

        Args:
            path: The file path to read from.
            encoding: The text encoding to use.

        Returns:
            str: The file contents.
        """

    @abstractmethod
    def chmod(self, path: Path, mode: int) -> None:
        """Change file permissions.

        Args:
            path: The file path.
            mode: The permission mode (e.g., 0o755).
        """

    @abstractmethod
    def remove(self, path: Path) -> None:
        """Remove a file.

        Args:
            path: The file path to remove.
        """

    @abstractmethod
    def remove_tree(self, path: Path) -> None:
        """Remove a directory tree recursively.

        Args:
            path: The directory path to remove.
        """

    @abstractmethod
    def glob(self, path: Path, pattern: str) -> list[Path]:
        """Glob for files matching a pattern under a directory.

        Args:
            path: The directory to search in.
            pattern: The glob pattern to match.

        Returns:
            list[Path]: List of matching paths.
        """

    @abstractmethod
    def rglob(self, path: Path, pattern: str) -> list[Path]:
        """Recursively glob for files matching a pattern under a directory.

        Args:
            path: The directory to search in.
            pattern: The glob pattern to match.

        Returns:
            list[Path]: List of matching paths.
        """

    @abstractmethod
    def is_file(self, path: Path) -> bool:
        """Check if a path is a file.

        Args:
            path: The path to check.

        Returns:
            bool: True if the path is a file, False otherwise.
        """

    @abstractmethod
    def iterdir(self, path: Path) -> list[Path]:
        """Iterate over the contents of a directory.

        Args:
            path: The directory path to iterate.

        Returns:
            list[Path]: List of paths within the directory (non-recursive).
        """


class RealFilesystem(FileSystemInterface):
    """Production implementation using pathlib and os operations."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def write_text(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        path.write_text(content, encoding=encoding)

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def chmod(self, path: Path, mode: int) -> None:
        path.chmod(mode)

    def remove(self, path: Path) -> None:
        path.unlink()

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return list(path.glob(pattern))

    def rglob(self, path: Path, pattern: str) -> list[Path]:
        return list(path.rglob(pattern))

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def iterdir(self, path: Path) -> list[Path]:
        return list(path.iterdir())


class InMemoryFilesystem(FileSystemInterface):
    """In-memory filesystem implementation for testing.

    This implementation stores file contents and metadata in memory,
    allowing fast, isolated tests without actual disk I/O.

    Note: Named InMemoryFilesystem (not TestFilesystem) to avoid pytest
    attempting to collect it as a test class.
    """

    def __init__(self) -> None:
        """Initialize the test filesystem."""
        self.files: dict[str, str] = {}  # path -> content
        self.permissions: dict[str, int] = {}  # path -> mode
        self.directories: set[str] = set()  # set of directory paths
        self.should_fail: dict[str, Exception] = {}  # path -> exception to raise

        # Initialize root directory to support relative paths
        self.directories.add(self._normalize_path(Path(".")))

    def simulate_permission_error(self, path: Path) -> None:
        """Configure the filesystem to raise PermissionError for a path.

        Args:
            path: The path that should fail with permission error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = PermissionError(f"Permission denied: '{path}'")

    def simulate_disk_full(self, path: Path) -> None:
        """Configure the filesystem to raise disk full error for a path.

        Args:
            path: The path that should fail with disk full error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = OSError(f"No space left on device: '{path}'")

    def simulate_readonly(self, path: Path) -> None:
        """Configure the filesystem to be read-only for a path.

        Args:
            path: The path that should fail with read-only error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = OSError(f"Read-only file system: '{path}'")

    def clear_failures(self) -> None:
        """Clear all simulated failures."""
        self.should_fail.clear()

    def _normalize_path(self, path: Path) -> str:
        """Normalize a path without making OS calls.

        Uses Path.absolute() for relative paths to anchor them, then
        applies pure PurePosixPath normalization to collapse '..' and '.'
        components without touching the real filesystem.
        """
        p = path if path.is_absolute() else Path.cwd() / path
        return posixpath.normpath(str(p))

    def exists(self, path: Path) -> bool:
        path_str = self._normalize_path(path)
        return path_str in self.files or path_str in self.directories

    def is_dir(self, path: Path) -> bool:
        path_str = self._normalize_path(path)
        return path_str in self.directories

    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str in self.directories:
            if not exist_ok:
                raise FileExistsError(f"Directory already exists: '{path}'")
            return

        if path_str in self.files:
            raise FileExistsError(f"File exists (not a directory): '{path}'")

        # Check parent exists if parents=False
        if not parents:
            parent = path.parent
            parent_str = self._normalize_path(parent)
            if parent_str != "." and parent_str not in self.directories:
                raise FileNotFoundError(f"Parent directory does not exist: '{parent}'")

        # Create directory and parents if needed
        if parents:
            current = path
            to_create: list[str] = []
            while True:
                current_str = self._normalize_path(current)
                if current_str == "." or current_str in self.directories:
                    break
                to_create.append(current_str)
                if current == current.parent:
                    break
                current = current.parent
            for dir_path in reversed(to_create):
                self.directories.add(dir_path)
        else:
            self.directories.add(path_str)

    def write_text(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str in self.directories:
            raise IsADirectoryError(f"Is a directory: '{path}'")

        # Ensure parent directory exists
        parent = path.parent
        parent_str = self._normalize_path(parent)
        if parent_str != "." and parent_str not in self.directories:
            raise FileNotFoundError(f"Parent directory does not exist: '{parent}'")

        self.files[path_str] = content
        if path_str not in self.permissions:
            self.permissions[path_str] = 0o644

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str in self.directories:
            raise IsADirectoryError(f"Is a directory: '{path}'")

        if path_str not in self.files:
            raise FileNotFoundError(f"No such file: '{path}'")

        return self.files[path_str]

    def chmod(self, path: Path, mode: int) -> None:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str not in self.files and path_str not in self.directories:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

        self.permissions[path_str] = mode

    def remove(self, path: Path) -> None:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str in self.directories:
            raise IsADirectoryError(f"Is a directory: '{path}'")

        if path_str not in self.files:
            raise FileNotFoundError(f"No such file: '{path}'")

        del self.files[path_str]
        if path_str in self.permissions:
            del self.permissions[path_str]

    def remove_tree(self, path: Path) -> None:
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str not in self.directories:
            raise FileNotFoundError(f"No such directory: '{path}'")

        prefix = path_str + "/"

        files_to_remove = [p for p in self.files if p == path_str or p.startswith(prefix)]
        for file_path in files_to_remove:
            del self.files[file_path]
            if file_path in self.permissions:
                del self.permissions[file_path]

        dirs_to_remove = [d for d in self.directories if d == path_str or d.startswith(prefix)]
        for dir_path in dirs_to_remove:
            self.directories.remove(dir_path)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        dir_str = self._normalize_path(path)
        prefix = dir_str + "/"
        results: list[Path] = []
        # Check all files and directories that are direct children matching
        for stored in list(self.files) + list(self.directories):
            if not stored.startswith(prefix):
                continue
            relative = stored[len(prefix) :]
            if fnmatch.fnmatch(relative, pattern):
                results.append(Path(stored))
        return results

    def rglob(self, path: Path, pattern: str) -> list[Path]:
        dir_str = self._normalize_path(path)
        prefix = dir_str + "/"
        results: list[Path] = []
        for stored in list(self.files) + list(self.directories):
            if not stored.startswith(prefix):
                continue
            relative = stored[len(prefix) :]
            # rglob matches pattern against any suffix of the relative path
            # e.g. rglob("*") matches all files/dirs recursively
            parts = relative.split("/")
            # Match the pattern against the full relative path using **/ prefix
            if (
                fnmatch.fnmatch(relative, pattern)
                or fnmatch.fnmatch(relative, "**/" + pattern)
                or fnmatch.fnmatch(parts[-1], pattern)
            ):
                results.append(Path(stored))
        return results

    def is_file(self, path: Path) -> bool:
        path_str = self._normalize_path(path)
        return path_str in self.files

    def iterdir(self, path: Path) -> list[Path]:
        dir_str = self._normalize_path(path)
        if dir_str not in self.directories:
            raise FileNotFoundError(f"No such directory: '{path}'")
        prefix = dir_str + "/"
        seen: set[str] = set()
        results: list[Path] = []
        for stored in list(self.files) + list(self.directories):
            if not stored.startswith(prefix):
                continue
            # Only direct children: no additional '/' after the prefix
            relative = stored[len(prefix) :]
            child_name = relative.split("/")[0]
            if child_name and child_name not in seen:
                seen.add(child_name)
                results.append(Path(dir_str) / child_name)
        return results
