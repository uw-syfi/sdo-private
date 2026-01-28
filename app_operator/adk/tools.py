import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Callable

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
            # Use iterdir() from Path is unsafe if we want to use InMemoryFilesystem fully,
            # but FileSystemInterface doesn't have listdir.
            # However, app_operator/langgraph/tools.py uses target.iterdir().
            # RealFilesystem relies on Path.iterdir().
            # InMemoryFilesystem doesn't implement iterdir() on path,
            # but the existing test suite must work somehow.
            # Let's check InMemoryFilesystem again. It stores paths in self.files/directories.
            # But context.resolve_path returns a Path object.
            # If we call target.iterdir(), it calls real filesystem.
            # So ls() in langgraph/tools.py is actually broken for InMemoryFilesystem unless
            # mocked or if target is not a real Path object.

            # To fix this properly for ADK (and keep consistent with plan),
            # I will use Path.iterdir() assuming RealFilesystem usage or
            # if InMemoryFilesystem is used, maybe we don't test ls() with it
            # or the tests mock Path.iterdir.
            # Wait, the plan for tests says:
            # "Use InMemoryFilesystem + stubbed AdkAgentRunner to simulate: ... Successful deploy"
            # Deploy usually doesn't use LS tool directly, but "Script Generator" might.

            # For now, I will use target.iterdir() to match langgraph implementation.
            entries = sorted(p.name for p in target.iterdir())
            result = "\n".join(entries)
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return ls


def _build_glob(context: ToolContext) -> Callable[[str], List[str]]:
    def glob(pattern: str) -> List[str]:
        """Find files matching the pattern."""
        try:
            if Path(pattern).is_absolute():
                try:
                    pattern = str(Path(pattern).relative_to(context.repo_root))
                except ValueError:
                    return [f"Error: Pattern escapes repository root: {pattern}"]

            results = []
            # Similarly, context.repo_root.glob(pattern) uses real filesystem
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
    def grep(pattern: str, path: str = ".") -> List[str]:
        """Search for a regex pattern in files."""
        try:
            target = context.resolve_path(path)
            regex = re.compile(pattern)
            matches: List[str] = []

            if context.filesystem.exists(target) and not context.filesystem.is_dir(
                target
            ):
                # We need to read content via filesystem interface
                try:
                    content = context.filesystem.read_text(target)
                    for idx, line in enumerate(content.splitlines(), start=1):
                        if regex.search(line):
                            relative = target.relative_to(context.repo_root)
                            matches.append(f"{relative}:{idx}:{line.strip()}")
                except Exception:
                    pass
                return matches

            # Recursive search needs to use real filesystem for walking?
            # Or we can't easily implement grep recursively with FileSystemInterface
            # as it lacks walk/glob methods.
            # Mirroring langgraph tool which uses target.rglob("*") (real fs).
            for file_path in target.rglob("*"):
                if file_path.is_file():
                    try:
                        # For rglob results (Path objects), we should route through filesystem interface
                        # to support InMemoryFilesystem if it had a way to lookup from Path.
                        # But InMemoryFilesystem stores strings.
                        # Since langgraph implementation effectively uses real FS for enumeration, I will too.
                        # But I will use context.filesystem.read_text for reading content.
                        content = context.filesystem.read_text(file_path)
                        for idx, line in enumerate(content.splitlines(), start=1):
                            if regex.search(line):
                                relative = file_path.relative_to(context.repo_root)
                                matches.append(f"{relative}:{idx}:{line.strip()}")
                    except Exception:
                        continue
            return matches
        except Exception as e:
            return [f"Error: {str(e)}"]

    return grep


def _build_write_file(context: ToolContext) -> Callable[[str, str], str]:
    def write_file(path: str, content: str) -> str:
        """Write content to a file."""
        try:
            target = context.resolve_path(path)
            if context.filesystem.is_dir(target):
                return f"Error: Path is a directory: {path}"

            # Use filesystem interface for mkdir
            # InMemoryFilesystem requires exact path matching for validation,
            # but mkdir in interface takes Path.

            # Check parent via interface? Interface doesn't have is_dir for parent check logic
            # inside mkdir usually handles it.
            if not context.filesystem.exists(target.parent):
                context.filesystem.mkdir(target.parent, parents=True, exist_ok=True)

            context.filesystem.write_text(target, content)
            result = f"Wrote {len(content)} bytes to {path}"
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return write_file


def _build_bash(context: ToolContext) -> Callable[[str, int], Dict[str, Any]]:
    def bash(command: str, timeout: int = 120) -> Dict[str, Any]:
        """Execute a bash command."""
        try:
            # subprocess uses real system
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


def _build_finish_deployment(context: ToolContext) -> Callable[[], str]:
    def finish_deployment() -> str:
        """Mark the deployment as successfully completed and finish the process."""
        return "DEPLOYMENT_FINISHED"

    return finish_deployment


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
        _build_finish_deployment(context),
    ]
