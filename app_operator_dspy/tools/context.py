"""Task context for DSPy operator tools.

Stores the current repository path so run_shell_tool always executes
in the correct working directory regardless of LLM-provided parameters.
"""

from contextvars import ContextVar

_task_repo: ContextVar[str | None] = ContextVar("task_repo", default=None)


def set_task_repo(path: str | None) -> None:
    """Set the repository path for the current task."""
    _task_repo.set(path)


def get_task_repo() -> str | None:
    """Return the current task's repository path, or None if not set."""
    return _task_repo.get()
