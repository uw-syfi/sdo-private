"""DSPy tool wrappers for ReAct agents.

All tools are consistently named with _tool suffix. Each exposes a stable
__name__ for the LLM (matching signatures) and returns strings or
string-like values for tool feedback.
"""

from app_operator_dspy.tools.filesystem import list_files, read_file, write_file
from app_operator_dspy.tools.health_check import run_health_check
from app_operator_dspy.tools.shell import DEFAULT_TIMEOUT, run_shell


def read_file_tool(path: str) -> str:
    """Read file contents. Returns content or error string."""
    return read_file(path)


def write_file_tool(path: str, content: str) -> str:
    """Write content to file. Returns success message or error string."""
    try:
        return write_file(path, content)
    except OSError as exc:
        return f"Error writing {path}: {exc}"


def list_files_tool(path: str, pattern: str = "*") -> str:
    """List files matching a glob pattern. Returns paths or error string."""
    return list_files(path, pattern)


def run_shell_tool(command: str, cwd: str = ".", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute shell command. Returns formatted output string."""
    result = run_shell(command, cwd=cwd, timeout=timeout)
    return result.output


def run_health_check_tool(repo_path: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run health check script. Returns output string."""
    return run_health_check(repo_path, timeout=timeout)


# Expose stable names for ReAct (LLM expects these from signatures)
read_file_tool.__name__ = "read_file"
write_file_tool.__name__ = "write_file"
list_files_tool.__name__ = "list_files"
run_shell_tool.__name__ = "run_shell"
run_health_check_tool.__name__ = "run_health_check"
