"""DSPy tool wrappers for ReAct agents.

run_shell_tool and write_file_tool are thin wrappers that add behaviour
(context injection and error-to-string conversion respectively).  All
other tools are used directly from their implementation modules.
"""

import functools

from app_operator_dspy.tools.context import get_task_repo
from app_operator_dspy.tools.filesystem import write_file
from app_operator_dspy.tools.shell import DEFAULT_TIMEOUT, run_shell


@functools.wraps(write_file)
def write_file_tool(path: str, content: str) -> str:
    """Write content to file. Returns success message or error string."""
    try:
        return write_file(path, content)
    except OSError as exc:
        return f"Error writing {path}: {exc}"


def run_shell_tool(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute shell command. Returns formatted output string.

    Runs with cwd set to the task's repo path (from operator context).
    Raises RuntimeError if task repo context is not set.
    """
    cwd = get_task_repo()
    if cwd is None:
        raise RuntimeError(
            "run_shell_tool called without task repo context; "
            "operator must call set_task_repo(repo_path) before agent invocation"
        )
    result = run_shell(command, cwd=cwd, timeout=timeout)
    return result.output


# Expose stable names for ReAct (LLM expects these from signatures)
write_file_tool.__name__ = "write_file"
run_shell_tool.__name__ = "run_shell"
