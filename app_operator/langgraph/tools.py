import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Callable

from langchain_core.tools import tool

from app_operator.filesystem import FileSystemInterface, RealFilesystem
from tools.trajectory import record_tool_call


class ToolContext:
    def __init__(self, repo_root: Path, filesystem: FileSystemInterface):
        self.repo_root = repo_root
        self.filesystem = filesystem

    def resolve_path(self, path: str) -> Path:
        candidate = (self.repo_root / path).resolve()
        if self.repo_root not in candidate.parents and candidate != self.repo_root:
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
            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                result = target.name
                record_tool_call(tool="LS", args={"path": path}, stdout=result)
                return result
            entries = sorted(p.name for p in target.iterdir())
            result = "\n".join(entries)
            record_tool_call(tool="LS", args={"path": path}, stdout=result)
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
            record_tool_call(
                tool="Glob", args={"pattern": pattern}, stdout="\n".join(results)
            )
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
            record_tool_call(tool="Read", args={"path": path}, stdout=content)
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

            if context.filesystem.exists(target) and not context.filesystem.is_dir(target):
                matches.extend(_grep_file(regex, target, context.repo_root))
                record_tool_call(
                    tool="Grep",
                    args={"pattern": pattern, "path": path},
                    stdout="\n".join(matches),
                )
                return matches

            for file_path in target.rglob("*"):
                if file_path.is_file():
                    matches.extend(_grep_file(regex, file_path, context.repo_root))
            record_tool_call(
                tool="Grep",
                args={"pattern": pattern, "path": path},
                stdout="\n".join(matches),
            )
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
            record_tool_call(
                tool="write_file",
                args={"path": path, "content": f"<len:{len(content)}>"},
                stdout=result,
            )
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return write_file


def _build_bash(context: ToolContext) -> Callable[[str, int], Dict[str, Any]]:
    @tool("bash")
    def bash(command: str, timeout: int = 120) -> Dict[str, Any]:
        """Execute a bash command."""
        try:
            result = subprocess.run(
                command,
                cwd=str(context.repo_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            record_tool_call(
                tool="bash",
                args={"command": command, "timeout": timeout},
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            error_msg = f"Command timed out after {timeout} seconds"
            record_tool_call(
                tool="bash",
                args={"command": command, "timeout": timeout},
                stdout="",
                stderr=error_msg,
                exit_code=-1,
            )
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": error_msg,
            }
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            record_tool_call(
                tool="bash",
                args={"command": command, "timeout": timeout},
                stdout="",
                stderr=error_msg,
                exit_code=-1,
            )
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
