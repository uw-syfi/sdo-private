"""Tool functions for pydantic_ai operator.

Plain functions with ``RunContext[OperatorDeps]`` as first parameter.
Same logic as ``langgraph/tools.py`` but idiomatic Pydantic AI.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from app_operator.command_validation import DangerousCommandError, validate_command
from app_operator.filesystem import FileSystemInterface
from app_operator.pydantic_ai._deps import OperatorDeps

SUBPROCESS_TIMEOUT_SECS = 120
OUTPUT_SPILL_THRESHOLD = 10_000


def ls_dir(ctx: RunContext[OperatorDeps], path: str = ".") -> str:
    """List files in the specified directory."""
    try:
        target = ctx.deps.resolve_path(path)
        if not ctx.deps.filesystem.exists(target):
            return f"Error: Path does not exist: {path}"
        if not ctx.deps.filesystem.is_dir(target):
            return target.name
        entries = sorted(p.name for p in ctx.deps.filesystem.iterdir(target))
        return "\n".join(entries)
    except (ValueError, OSError) as e:
        return f"Error: {e!s}"


def glob_files(ctx: RunContext[OperatorDeps], pattern: str) -> list[str]:
    """Find files matching the pattern."""
    try:
        if Path(pattern).is_absolute():
            try:
                pattern = str(Path(pattern).relative_to(ctx.deps.repo_path))
            except ValueError:
                return [f"Error: Pattern escapes repository root: {pattern}"]

        results = []
        for path in ctx.deps.filesystem.glob(ctx.deps.repo_path, pattern):
            if ctx.deps.filesystem.is_file(path) or ctx.deps.filesystem.is_dir(path):
                try:
                    relative = path.relative_to(ctx.deps.repo_path)
                    results.append(str(relative))
                except ValueError:
                    continue
        return sorted(results)
    except (ValueError, OSError) as e:
        return [f"Error: {e!s}"]


def read_file(ctx: RunContext[OperatorDeps], path: str, start_line: int, end_line: int) -> str:
    """Read a range of lines from a file (1-based, inclusive).

    Args:
        ctx: Run context with operator dependencies.
        path: Path to the file.
        start_line: First line to return (1-based).
        end_line: Last line to return (1-based, inclusive).
    """
    try:
        target = ctx.deps.resolve_path(path)
        content = ctx.deps.filesystem.read_text(target)
        lines = content.splitlines(keepends=True)
        selected = lines[start_line - 1 : end_line]
        return "".join(selected)
    except (ValueError, OSError) as e:
        return f"Error: {e!s}"


def _grep_file(regex: re.Pattern, file_path: Path, repo_root: Path, filesystem: FileSystemInterface) -> list[str]:
    results = []
    try:
        content = filesystem.read_text(file_path)
    except OSError:
        return results
    for idx, line in enumerate(content.splitlines(), start=1):
        if regex.search(line):
            relative = file_path.relative_to(repo_root)
            results.append(f"{relative}:{idx}:{line.strip()}")
    return results


def grep(ctx: RunContext[OperatorDeps], pattern: str, path: str = ".") -> list[str]:
    """Search for a regex pattern in files."""
    try:
        target = ctx.deps.resolve_path(path)
        regex = re.compile(pattern)
        matches: list[str] = []

        if ctx.deps.filesystem.exists(target) and not ctx.deps.filesystem.is_dir(target):
            matches.extend(_grep_file(regex, target, ctx.deps.repo_path, ctx.deps.filesystem))
            return matches

        for file_path in ctx.deps.filesystem.rglob(target, "*"):
            if ctx.deps.filesystem.is_file(file_path):
                matches.extend(_grep_file(regex, file_path, ctx.deps.repo_path, ctx.deps.filesystem))
        return matches
    except (ValueError, OSError, re.error) as e:
        return [f"Error: {e!s}"]


def write_file(ctx: RunContext[OperatorDeps], path: str, content: str) -> str:
    """Write content to a file."""
    try:
        target = ctx.deps.resolve_path(path)
        if ctx.deps.filesystem.is_dir(target):
            return f"Error: Path is a directory: {path}"
        if not ctx.deps.filesystem.exists(target.parent):
            ctx.deps.filesystem.mkdir(target.parent, parents=True, exist_ok=True)
        ctx.deps.filesystem.write_text(target, content)
        return f"Wrote {len(content)} bytes to {path}"
    except (ValueError, OSError) as e:
        return f"Error: {e!s}"


def str_replace(ctx: RunContext[OperatorDeps], path: str, old_str: str, new_str: str) -> str:
    """Replace an exact string in a file with new content.

    Finds old_str in the file and replaces it with new_str. Fails if
    old_str appears zero times (not found) or more than once (ambiguous).

    Args:
        ctx: Run context with operator dependencies.
        path: Path to the file to edit.
        old_str: The exact string to find and replace.
        new_str: The string to replace old_str with.
    """
    try:
        target = ctx.deps.resolve_path(path)
        content = ctx.deps.filesystem.read_text(target)
        count = content.count(old_str)
        if count == 0:
            return f"Error: old_str not found in {path}"
        if count > 1:
            return f"Error: old_str appears {count} times in {path} (must be unique)"
        new_content = content.replace(old_str, new_str, 1)
        ctx.deps.filesystem.write_text(target, new_content)
        return f"Edited {path}"
    except (ValueError, OSError) as e:
        return f"Error: {e!s}"


def bash(ctx: RunContext[OperatorDeps], command: str, timeout: int = SUBPROCESS_TIMEOUT_SECS) -> dict[str, Any]:
    """Execute a bash command."""
    try:
        validate_command(command)
        result = subprocess.run(  # noqa: S602 — shell=True required for agent commands
            command,
            cwd=str(ctx.deps.repo_path),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout = result.stdout
        stderr = result.stderr

        if len(stdout) + len(stderr) > OUTPUT_SPILL_THRESHOLD:
            call_id = ctx.deps.next_tool_call_id()
            spill_dir = ctx.deps.repo_path / ".sds" / "logs" / "tools" / f"{call_id:04d}"
            ctx.deps.filesystem.mkdir(spill_dir, parents=True, exist_ok=True)

            stdout_path = f".sds/logs/tools/{call_id:04d}/stdout.txt"
            ctx.deps.filesystem.write_text(spill_dir / "stdout.txt", stdout)
            stdout = f"(output too large for context; use read_file tool: {stdout_path})"

            if stderr:
                stderr_path = f".sds/logs/tools/{call_id:04d}/stderr.txt"
                ctx.deps.filesystem.write_text(spill_dir / "stderr.txt", stderr)
                stderr = f"(output too large for context; use read_file tool: {stderr_path})"

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except DangerousCommandError as e:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": f"Command timed out after {timeout} seconds"}
    except OSError as e:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": f"Error: {e!s}"}


def build_tools() -> list[Callable]:
    """Return list of tool functions."""
    return [
        ls_dir,
        glob_files,
        read_file,
        grep,
        write_file,
        str_replace,
        bash,
    ]
