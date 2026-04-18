"""Generic shell and file tools used by all Crucible agents."""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shlex
import signal
import subprocess
import uuid
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for pydantic-ai tool introspection

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000
MAX_READ_CHARS = 25000
APPLY_READ_CHAR_LIMIT = True
BASH_TIMEOUT = 60
MUTATING_KUBECTL_VERBS: frozenset[str] = frozenset(
    {
        "apply",
        "delete",
        "patch",
        "edit",
        "scale",
        "set",
        "replace",
        "create",
        "rollout",
    }
)
MAX_GREP_RESULTS = 200

# Directories skipped during recursive grep to avoid scanning virtualenvs,
# caches, and other large non-source trees.
_GREP_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".env",
        ".git",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        "dist",
        "build",
    }
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _agent_cwd() -> Path:
    """Return the working directory for agent tools (from ``SREGYM_EXP_ENV`` or ``'.'``)."""
    return Path(os.getenv("SREGYM_EXP_ENV", "."))


def run_bash_sync(cmd: str) -> str:
    """Run *cmd* in a shell, capture stdout+stderr, truncate to MAX_OUTPUT_CHARS.

    The subprocess is started in its own session (``start_new_session=True``)
    so that on timeout we can kill the **entire process group** — not just the
    top-level shell — preventing orphaned child processes such as
    ``kubectl exec -it`` from lingering indefinitely.
    """
    cwd = str(_agent_cwd())
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S602
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=BASH_TIMEOUT)
        output = stdout
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        if process.returncode != 0:
            output = f"[Exit code {process.returncode}]\n{output}"
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait()
        return f"Error: Command timed out after {BASH_TIMEOUT} seconds."
    except Exception as e:
        if process is not None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except OSError:
                process.kill()
            process.wait()
        return f"Error executing command: {e}"

    if len(output) > MAX_OUTPUT_CHARS:
        tmp_path = f"/tmp/bash_out_{uuid.uuid4().hex}.txt"
        Path(tmp_path).write_text(output)
        return (
            f"Output truncated ({len(output)} chars). Written to {tmp_path}. "
            f"Use read_file to inspect it (e.g. read_file(path='{tmp_path}', start_line=0, end_line=100))."
        )
    return output or "(no output)"


def check_mutating_kubectl(cmd: str) -> str | None:
    """Return an error message if *cmd* contains a mutating kubectl verb, else None."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return "Error: unable to parse command (malformed quoting). Fix the command syntax."

    for i, token in enumerate(tokens):
        if token == "kubectl":
            # Find the first non-flag token after "kubectl"
            for candidate in tokens[i + 1 :]:
                if not candidate.startswith("-"):
                    if candidate in MUTATING_KUBECTL_VERBS:
                        return (
                            f"Error: kubectl verb '{candidate}' is mutating and not allowed "
                            "for the judge agent. Use exec_bash_readonly only for read-only operations."
                        )
                    break
    return None


def exec_bash_readonly_impl(cmd: str) -> str:
    """Core read-only bash execution: check for mutating kubectl, then run."""
    error = check_mutating_kubectl(cmd)
    if error is not None:
        return error
    return run_bash_sync(cmd)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def exec_bash(ctx: RunContext[Any], cmd: str) -> str:
    """Execute a shell command and return its output.

    Args:
        cmd: The shell command to run.
    """
    return run_bash_sync(cmd)


def read_file_impl(
    path: str,
    start_line: int = 0,
    end_line: int | None = None,
) -> str:
    """Read lines from a file and return them in cat -n format.

    Args:
        path: Absolute or relative path to the file.
        start_line: First line to read (0-indexed, inclusive). Negative values
            count from the end of the file (e.g. -50 starts at the 50th-last line).
        end_line: Last line to read (0-indexed, exclusive). Defaults to 200
            when start_line >= 0, or end-of-file when start_line < 0.
            Use -1 to explicitly read through the end of the file.
    """
    try:
        lines = Path(path).read_text().splitlines()
        n = len(lines)
        start = max(0, start_line if start_line >= 0 else n + start_line)
        if end_line is None:
            end = n if start_line < 0 else min(200, n)
        elif end_line < 0:
            end = n
        else:
            end = min(end_line, n)
        numbered = "\n".join(f"{start + i + 1:6}\t{line}" for i, line in enumerate(lines[start:end]))
        if not numbered:
            return "(empty range)"
        if APPLY_READ_CHAR_LIMIT and len(numbered) > MAX_READ_CHARS:
            return (
                f"Error: requested range too large ({len(numbered)} chars, max {MAX_READ_CHARS}). "
                f"The file has {len(lines)} lines. Either proportionally decrease the line range "
                f"based on {len(numbered)}/{MAX_READ_CHARS} chars, or read just a few lines first "
                f"to find keywords, then use `cat {path} | grep <pattern>` via exec_bash to "
                f"extract only the lines you need."
            )
        return numbered
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


def read_file(
    ctx: RunContext[Any],
    path: str,
    start_line: int = 0,
    end_line: int | None = None,
) -> str:
    """Read lines from a file and return them in cat -n format.

    Args:
        path: Absolute or relative path to the file.
        start_line: First line to read (0-indexed, inclusive). Negative values
            count from the end of the file (e.g. -50 starts at the 50th-last line).
        end_line: Last line to read (0-indexed, exclusive). Defaults to 200
            when start_line >= 0, or end-of-file when start_line < 0.
            Use -1 to explicitly read through the end of the file.
    """
    return read_file_impl(path, start_line, end_line)


def grep_impl(
    pattern: str,
    path: str = ".",
    include: str = "",
) -> str:
    """Search for a regex pattern in files, returning matching lines with file paths and line numbers.

    Args:
        pattern: Regex pattern to search for.
        path: File or directory to search in (default: current working directory).
        include: Optional glob pattern to filter files (e.g. "*.yaml", "*.py").
    """
    target = _agent_cwd() / path
    if len(pattern) > 1000:
        return "Error: regex pattern too long (max 1000 chars)."
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    if not target.exists():
        return f"Error: path not found: {path}"

    matches: list[str] = []
    total_chars = 0
    truncated = False

    files: list[Path]
    if target.is_file():
        files = [target]
    else:
        glob_pattern = include or "*"
        collected: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [d for d in sorted(dirnames) if d not in _GREP_SKIP_DIRS]
            collected.extend(Path(dirpath) / fn for fn in sorted(filenames) if fnmatch.fnmatch(fn, glob_pattern))
        files = collected

    for file_path in files:
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                try:
                    relative = file_path.relative_to(target)
                except ValueError:
                    relative = file_path
                entry = f"{relative}:{idx}:{line.rstrip()}"
                total_chars += len(entry) + 1
                if total_chars > MAX_OUTPUT_CHARS or len(matches) >= MAX_GREP_RESULTS:
                    truncated = True
                    break
                matches.append(entry)
        if truncated:
            break

    if not matches:
        return "(no matches)"

    result = "\n".join(matches)
    if truncated:
        tmp_path = f"/tmp/grep_out_{uuid.uuid4().hex}.txt"
        Path(tmp_path).write_text(result)
        result += (
            f"\n\n... truncated ({len(matches)} matches shown). "
            f"Full output written to {tmp_path}. "
            f"Use read_file to inspect it."
        )
    return result


def grep(
    ctx: RunContext[Any],
    pattern: str,
    path: str = ".",
    include: str = "",
) -> str:
    """Search for a regex pattern in files, returning matching lines with file paths and line numbers.

    Args:
        pattern: Regex pattern to search for.
        path: File or directory to search in (default: current working directory).
        include: Optional glob pattern to filter files (e.g. "*.yaml", "*.py").
    """
    return grep_impl(pattern, path, include)


def write_file_impl(path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def write_file(ctx: RunContext[Any], path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    return write_file_impl(path, content)


def str_replace_file_impl(
    path: str,
    old_str: str,
    new_str: str,
) -> str:
    """Replace the first occurrence of old_str with new_str in a file.

    Args:
        path: Path to the file to edit.
        old_str: Exact string to find (must appear at least once).
        new_str: Replacement string.
    """
    try:
        if not old_str:
            return "Error: old_str must not be empty."
        p = Path(path)
        original = p.read_text()
        if old_str not in original:
            return f"Error: old_str not found in {path}"
        updated = original.replace(old_str, new_str, 1)
        p.write_text(updated)
        return f"Replaced in {path}"
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except Exception as e:
        return f"Error replacing in file: {e}"


def str_replace_file(
    ctx: RunContext[Any],
    path: str,
    old_str: str,
    new_str: str,
) -> str:
    """Replace the first occurrence of old_str with new_str in a file.

    Args:
        path: Path to the file to edit.
        old_str: Exact string to find (must appear at least once).
        new_str: Replacement string.
    """
    return str_replace_file_impl(path, old_str, new_str)


def exec_bash_readonly(ctx: RunContext[Any], cmd: str) -> str:
    """Execute a read-only shell command. Mutating kubectl verbs are blocked.

    Args:
        cmd: The shell command to run (must not mutate cluster state).
    """
    return exec_bash_readonly_impl(cmd)
