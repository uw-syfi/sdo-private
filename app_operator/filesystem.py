"""Filesystem abstraction layer for testability.

This module provides an abstraction over filesystem operations to enable
dependency injection and testing without actual file I/O.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


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
        pass

    @abstractmethod
    def is_dir(self, path: Path) -> bool:
        """Check if a path is a directory.

        Args:
            path: The path to check.

        Returns:
            bool: True if the path is a directory, False otherwise.
        """
        pass

    @abstractmethod
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True):
        """Create a directory.

        Args:
            path: The directory path to create.
            parents: If True, create parent directories as needed.
            exist_ok: If True, don't raise an error if the directory exists.
        """
        pass

    @abstractmethod
    def write_text(self, path: Path, content: str, encoding: str = "utf-8"):
        """Write text to a file.

        Args:
            path: The file path to write to.
            content: The text content to write.
            encoding: The text encoding to use.
        """
        pass

    @abstractmethod
    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        """Read text from a file.

        Args:
            path: The file path to read from.
            encoding: The text encoding to use.

        Returns:
            str: The file contents.
        """
        pass

    @abstractmethod
    def chmod(self, path: Path, mode: int):
        """Change file permissions.

        Args:
            path: The file path.
            mode: The permission mode (e.g., 0o755).
        """
        pass

    @abstractmethod
    def remove(self, path: Path):
        """Remove a file.

        Args:
            path: The file path to remove.
        """
        pass

    @abstractmethod
    def remove_tree(self, path: Path):
        """Remove a directory tree recursively.

        Args:
            path: The directory path to remove.
        """
        pass


class RealFilesystem(FileSystemInterface):
    """Production implementation using pathlib and os operations."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True):
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def write_text(self, path: Path, content: str, encoding: str = "utf-8"):
        path.write_text(content, encoding=encoding)

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def chmod(self, path: Path, mode: int):
        path.chmod(mode)

    def remove(self, path: Path):
        path.unlink()

    def remove_tree(self, path: Path):
        import shutil

        shutil.rmtree(path)


class InMemoryFilesystem(FileSystemInterface):
    """In-memory filesystem implementation for testing.

    This implementation stores file contents and metadata in memory,
    allowing fast, isolated tests without actual disk I/O.

    Note: Named InMemoryFilesystem (not TestFilesystem) to avoid pytest
    attempting to collect it as a test class.
    """

    def __init__(self):
        """Initialize the test filesystem."""
        self.files: Dict[str, str] = {}  # path -> content
        self.permissions: Dict[str, int] = {}  # path -> mode
        self.directories: set = set()  # set of directory paths
        self.should_fail: Dict[str, Exception] = {}  # path -> exception to raise

        # Initialize root directory to support relative paths
        self.directories.add(self._normalize_path(Path(".")))

    def simulate_permission_error(self, path: Path):
        """Configure the filesystem to raise PermissionError for a path.

        Args:
            path: The path that should fail with permission error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = PermissionError(f"Permission denied: '{path}'")

    def simulate_disk_full(self, path: Path):
        """Configure the filesystem to raise disk full error for a path.

        Args:
            path: The path that should fail with disk full error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = OSError(f"No space left on device: '{path}'")

    def simulate_readonly(self, path: Path):
        """Configure the filesystem to be read-only for a path.

        Args:
            path: The path that should fail with read-only error.
        """
        path_str = self._normalize_path(path)
        self.should_fail[path_str] = OSError(f"Read-only file system: '{path}'")

    def clear_failures(self):
        """Clear all simulated failures."""
        self.should_fail.clear()

    def _normalize_path(self, path: Path) -> str:
        """Normalize a path to handle resolved paths consistently."""
        try:
            # Resolve the path to handle symlinks and relative paths
            resolved = path.resolve()
            return str(resolved)
        except (OSError, RuntimeError):
            # If resolve fails (e.g., path doesn't exist), use as-is
            return str(path)

    def exists(self, path: Path) -> bool:
        path_str = self._normalize_path(path)
        return path_str in self.files or path_str in self.directories

    def is_dir(self, path: Path) -> bool:
        path_str = self._normalize_path(path)
        return path_str in self.directories

    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True):
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
            to_create = []
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

    def write_text(self, path: Path, content: str, encoding: str = "utf-8"):
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

    def chmod(self, path: Path, mode: int):
        path_str = self._normalize_path(path)

        if path_str in self.should_fail:
            raise self.should_fail[path_str]

        if path_str not in self.files and path_str not in self.directories:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

        self.permissions[path_str] = mode

    def remove(self, path: Path):
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

    def remove_tree(self, path: Path):
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
