import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from libs.sds_core.command_validation import DangerousCommandError, validate_command
from libs.sds_core.filesystem import FileSystemInterface, RealFilesystem


class ToolContext:
    def __init__(self, repo_root: Path, filesystem: FileSystemInterface):
        self.repo_root = repo_root
        self.filesystem = filesystem

    def resolve_path(self, path: str) -> Path:
        # Construct path without checking filesystem (for InMemoryFilesystem support)
        # Use absolute() which constructs path without filesystem checks
        path_obj = Path(path)
        if path_obj.is_absolute():
            candidate = path_obj
        else:
            candidate = self.repo_root / path

        # Normalize .. and . without filesystem access
        parts: list[str] = []
        for part in candidate.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)

        if parts:
            candidate = Path(*parts)
        else:
            candidate = Path("/")

        # Check if path escapes repository root
        try:
            candidate.relative_to(self.repo_root)
        except ValueError:
            raise ValueError(f"Path escapes repository root: {path}") from None

        return candidate


def _build_list_files(context: ToolContext) -> Callable[[str], dict[str, Any]]:
    def list_files(path: str) -> dict[str, Any]:
        """List files in the specified directory.

        Args:
            path: The directory path to list.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if the directory exists, 'error' otherwise.
            - 'output': A newline-separated list of file names if successful.
            - 'error': Error message if status is 'error'.
            - 'context': Metadata including 'path' and 'count' (number of files) or 'type'.
        """
        try:
            target = context.resolve_path(path)
            if not context.filesystem.exists(target):
                return {
                    "status": "error",
                    "error": f"Path does not exist: {path}",
                    "context": {"path": path},
                }
            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                return {
                    "status": "success",
                    "output": target.name,
                    "context": {"path": path, "type": "file"},
                }

            # Use iterdir() from Path is unsafe if we want to use InMemoryFilesystem fully,
            # but FileSystemInterface doesn't have listdir.
            # However, app_operator/langgraph/tools.py uses target.iterdir().
            # RealFilesystem relies on Path.iterdir().
            def _to_relative(p: Path) -> str:
                try:
                    return str(p.relative_to(context.repo_root))
                except ValueError:
                    return p.name

            entries = sorted(_to_relative(p) for p in target.iterdir())
            result = "\n".join(entries)
            return {
                "status": "success",
                "output": result,
                "context": {"path": path, "count": len(entries)},
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "context": {"path": path},
            }

    return list_files


def _build_find_files(context: ToolContext) -> Callable[[str], dict[str, Any]]:
    def find_files(pattern: str) -> dict[str, Any]:
        """Find files matching the pattern.

        Args:
            pattern: The glob pattern to search for.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if the search completed, 'error' otherwise.
            - 'output': A newline-separated list of matching file paths relative to repo root.
            - 'error': Error message if status is 'error'.
            - 'context': Metadata including 'pattern' and 'count' (number of matches).
        """
        try:
            if Path(pattern).is_absolute():
                try:
                    pattern = str(Path(pattern).relative_to(context.repo_root))
                except ValueError:
                    return {
                        "status": "error",
                        "error": f"Pattern escapes repository root: {pattern}",
                        "context": {"pattern": pattern},
                    }

            results: list[str] = []
            # Similarly, context.repo_root.glob(pattern) uses real filesystem
            for path in context.repo_root.glob(pattern):
                if path.is_file() or path.is_dir():
                    try:
                        relative = path.relative_to(context.repo_root)
                        results.append(str(relative))
                    except ValueError:
                        continue
            results = sorted(results)
            return {
                "status": "success",
                "output": "\n".join(results),
                "context": {"pattern": pattern, "count": len(results)},
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "context": {"pattern": pattern},
            }

    return find_files


def _build_read_file(context: ToolContext) -> Callable[[str], dict[str, Any]]:
    def read_file(path: str) -> dict[str, Any]:
        """Read the content of a file.

        Args:
            path: The path to the file to read.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if the file was read, 'error' otherwise.
            - 'output': The content of the file if successful.
            - 'error': Error message if status is 'error'.
            - 'context': Metadata including 'path' and 'bytes' (size of content).
        """
        try:
            target = context.resolve_path(path)
            content = context.filesystem.read_text(target)
            return {
                "status": "success",
                "output": content,
                "context": {"path": path, "bytes": len(content)},
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "context": {"path": path},
            }

    return read_file


def _build_search_content(context: ToolContext) -> Callable[[str, str], dict[str, Any]]:
    def search_content(pattern: str, path: str) -> dict[str, Any]:
        """Search for a regex pattern in files.

        Args:
            pattern: The regex pattern to search for.
            path: The directory or file path to search in.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if the search completed, 'error' otherwise.
            - 'output': A newline-separated list of matches in format 'path:line:content'.
            - 'error': Error message if status is 'error'.
            - 'context': Metadata including 'pattern', 'path', and 'count' (number of matches).
        """
        try:
            target = context.resolve_path(path)
            regex = re.compile(pattern)
            matches: list[str] = []

            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                # We need to read content via filesystem interface
                try:
                    content = context.filesystem.read_text(target)
                    for idx, line in enumerate(content.splitlines(), start=1):
                        if regex.search(line):
                            relative = target.relative_to(context.repo_root)
                            matches.append(f"{relative}:{idx}:{line.strip()}")
                except Exception:
                    pass
                return {
                    "status": "success",
                    "output": "\n".join(matches),
                    "context": {
                        "pattern": pattern,
                        "path": path,
                        "count": len(matches),
                    },
                }

            # Recursive search over files under the target path.
            # We enumerate with Path.rglob (real filesystem), but always read file
            # contents through the injected filesystem interface.
            for file_path in target.rglob("*"):
                if file_path.is_file():
                    try:
                        # Normalize to a real Path constructed from the string form.
                        # This makes tests that mock Path objects (with __str__ set
                        # to the in-memory key) work correctly with InMemoryFilesystem.
                        fs_path = Path(str(file_path))
                        content = context.filesystem.read_text(fs_path)
                        for idx, line in enumerate(content.splitlines(), start=1):
                            if regex.search(line):
                                relative = fs_path.relative_to(context.repo_root)
                                matches.append(f"{relative}:{idx}:{line.strip()}")
                    except Exception:
                        continue
            return {
                "status": "success",
                "output": "\n".join(matches),
                "context": {"pattern": pattern, "path": path, "count": len(matches)},
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "context": {"pattern": pattern, "path": path},
            }

    return search_content


def _build_write_file(context: ToolContext) -> Callable[[str, str], dict[str, Any]]:
    def write_file(path: str, content: str) -> dict[str, Any]:
        """Write content to a file.

        Args:
            path: The path to the file to write.
            content: The content to write.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if the file was written, 'error' otherwise.
            - 'output': A success message indicating bytes written.
            - 'error': Error message if status is 'error'.
            - 'context': Metadata including 'path' and 'bytes' (size of written content).
        """
        try:
            target = context.resolve_path(path)
            if context.filesystem.is_dir(target):
                return {
                    "status": "error",
                    "error": f"Path is a directory: {path}",
                    "context": {"path": path},
                }

            # Use filesystem interface for mkdir
            # InMemoryFilesystem requires exact path matching for validation,
            # but mkdir in interface takes Path.

            # Check parent via interface? Interface doesn't have is_dir for parent check logic
            # inside mkdir usually handles it.
            if not context.filesystem.exists(target.parent):
                context.filesystem.mkdir(target.parent, parents=True, exist_ok=True)

            context.filesystem.write_text(target, content)
            result = f"Wrote {len(content)} bytes to {path}"
            return {
                "status": "success",
                "output": result,
                "context": {"path": path, "bytes": len(content)},
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "context": {"path": path},
            }

    return write_file


def _build_run_command(context: ToolContext) -> Callable[[str, int], dict[str, Any]]:
    def run_command(command: str, timeout: int) -> dict[str, Any]:
        """Execute a bash command.

        Args:
            command: The command to execute.
            timeout: The maximum time to wait for the command to complete.

        Returns:
            A dictionary containing the operation result:
            - 'status': 'success' if exit code is 0, 'error' otherwise.
            - 'output': Standard output (stdout) if success, or standard error (stderr) if failed.
            - 'error': Standard error (stderr) if status is 'error', else None.
            - 'context': Metadata including 'command', 'exit_code', 'stdout', and 'stderr'.
        """
        try:
            validate_command(command)
            # subprocess uses real system
            result = subprocess.run(  # noqa: S602
                command,
                cwd=str(context.repo_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            success = result.returncode == 0
            return {
                "status": "success" if success else "error",
                "output": result.stdout if success else result.stderr,
                "error": result.stderr if not success else None,
                "context": {
                    "command": command,
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            }
        except DangerousCommandError as e:
            error_msg = str(e)
            return {
                "status": "error",
                "error": error_msg,
                "output": "",
                "context": {
                    "command": command,
                    "exit_code": -1,
                    "stderr": error_msg,
                },
            }
        except subprocess.TimeoutExpired:
            error_msg = f"Command timed out after {timeout} seconds"
            return {
                "status": "error",
                "error": error_msg,
                "output": "",
                "context": {
                    "command": command,
                    "exit_code": -1,
                    "stderr": error_msg,
                },
            }
        except Exception as e:
            error_msg = f"Error: {e!s}"
            return {
                "status": "error",
                "error": error_msg,
                "output": "",
                "context": {
                    "command": command,
                    "exit_code": -1,
                    "stderr": error_msg,
                },
            }

    return run_command


def build_tools(
    repo_path: Path, filesystem: FileSystemInterface | None = None
) -> list[Callable[..., Any]]:
    if filesystem is None:
        filesystem = RealFilesystem()

    context = ToolContext(repo_root=repo_path.resolve(), filesystem=filesystem)
    return [
        _build_list_files(context),
        _build_find_files(context),
        _build_read_file(context),
        _build_search_content(context),
        _build_write_file(context),
        _build_run_command(context),
    ]


def build_readonly_tools(repo_path: Path, filesystem: FileSystemInterface | None = None) -> list[Callable[..., Any]]:
    """Build read-only tools for external consumers (e.g. lego_agent).

    Returns tools for reading the repository without any write or execute access.
    """
    if filesystem is None:
        filesystem = RealFilesystem()
    context = ToolContext(repo_root=repo_path.resolve(), filesystem=filesystem)
    return [
        _build_read_file(context),
        _build_list_files(context),
        _build_find_files(context),
        _build_search_content(context),
    ]
