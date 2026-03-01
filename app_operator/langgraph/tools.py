import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Callable

from langchain_core.tools import tool

from app_operator.command_validation import DangerousCommandError, validate_command
from app_operator.filesystem import FileSystemInterface, RealFilesystem


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
        parts = []
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
            raise ValueError(f"Path escapes repository root: {path}")

        return candidate


def _build_ls(context: ToolContext) -> Callable[[str], str]:
    @tool("LS")
    def ls(path: str = ".") -> str:
        """List files in the specified directory."""
        try:
            target = context.resolve_path(path)
            if not context.filesystem.exists(target):
                return f"Error: Path does not exist: {path}"
            if context.filesystem.exists(target) and not context.filesystem.is_dir(
                target
            ):
                result = target.name
                return result
            entries = sorted(p.name for p in target.iterdir())
            result = "\n".join(entries)
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return ls


def _build_glob(context: ToolContext) -> Callable[[str], List[str]]:
    @tool("Glob")
    def glob(pattern: str) -> List[str]:
        """Find files matching the pattern."""
        try:
            if Path(pattern).is_absolute():
                try:
                    pattern = str(Path(pattern).relative_to(context.repo_root))
                except ValueError:
                    return [f"Error: Pattern escapes repository root: {pattern}"]

            results = []
            for path in context.repo_root.glob(pattern):
                if path.is_file() or path.is_dir():
                    try:
                        relative = path.relative_to(context.repo_root)
                        results.append(str(relative))
                    except ValueError:
                        continue
            results = sorted(results)
            return results
        except Exception as e:
            return [f"Error: {str(e)}"]

    return glob


def _build_read(context: ToolContext) -> Callable[[str], str]:
    @tool("Read")
    def read(path: str) -> str:
        """Read the content of a file."""
        try:
            target = context.resolve_path(path)
            content = context.filesystem.read_text(target)
            return content
        except Exception as e:
            return f"Error: {str(e)}"

    return read


def _build_grep(context: ToolContext) -> Callable[[str, str], List[str]]:
    @tool("Grep")
    def grep(pattern: str, path: str = ".") -> List[str]:
        """Search for a regex pattern in files."""
        try:
            target = context.resolve_path(path)
            regex = re.compile(pattern)
            matches: List[str] = []

            if context.filesystem.exists(target) and not context.filesystem.is_dir(
                target
            ):
                matches.extend(_grep_file(regex, target, context.repo_root))
                return matches

            for file_path in target.rglob("*"):
                if file_path.is_file():
                    matches.extend(_grep_file(regex, file_path, context.repo_root))
            return matches
        except Exception as e:
            return [f"Error: {str(e)}"]

    return grep


def _grep_file(regex: re.Pattern, file_path: Path, repo_root: Path) -> List[str]:
    results = []
    try:
        content = file_path.read_text(errors="ignore")
    except Exception:
        return results

    for idx, line in enumerate(content.splitlines(), start=1):
        if regex.search(line):
            relative = file_path.relative_to(repo_root)
            results.append(f"{relative}:{idx}:{line.strip()}")
    return results


def _build_write_file(context: ToolContext) -> Callable[[str, str], str]:
    @tool("write_file")
    def write_file(path: str, content: str) -> str:
        """Write content to a file."""
        try:
            target = context.resolve_path(path)
            if context.filesystem.is_dir(target):
                return f"Error: Path is a directory: {path}"
            if not context.filesystem.exists(target.parent):
                context.filesystem.mkdir(target.parent, parents=True, exist_ok=True)
            context.filesystem.write_text(target, content)
            result = f"Wrote {len(content)} bytes to {path}"
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return write_file


def _build_bash(context: ToolContext) -> Callable[[str, int], Dict[str, Any]]:
    @tool("bash")
    def bash(command: str, timeout: int = 120) -> Dict[str, Any]:
        """Execute a bash command."""
        try:
            validate_command(command)
            result = subprocess.run(
                command,
                cwd=str(context.repo_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except DangerousCommandError as e:
            error_msg = str(e)
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }
        except subprocess.TimeoutExpired:
            error_msg = f"Command timed out after {timeout} seconds"
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }

    return bash


def build_tools(
    repo_path: Path, filesystem: FileSystemInterface = None
) -> List[Callable[..., Any]]:
    if filesystem is None:
        filesystem = RealFilesystem()

    context = ToolContext(repo_root=repo_path.resolve(), filesystem=filesystem)
    return [
        _build_ls(context),
        _build_glob(context),
        _build_read(context),
        _build_grep(context),
        _build_write_file(context),
        _build_bash(context),
    ]
