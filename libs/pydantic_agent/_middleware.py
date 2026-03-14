"""AgentMiddleware base class."""

from __future__ import annotations

from typing import Any


class AgentMiddleware:
    """Base class for BaseAgent tool call middleware.

    Override any of the hook methods to observe or control agent execution.

    - ``before_tool_call`` / ``after_tool_call``: called around each tool invocation.
      ``before_tool_call`` is called in registration order; ``after_tool_call`` in reverse
      (outermost middleware wraps innermost).
    - ``after_run``: called once per ``_run()`` invocation after ``run_sync()`` completes.
      Called in registration order.
    """

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Called before each tool call. Return False to reject the call. Default: allow."""
        return True

    def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Called after each tool call completes. Default: no-op."""

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        """Called once after each agent run completes.

        Args:
            result: The ``RunResult`` returned by ``agent.run_sync()``.
            run_ctx: Optional context dict passed through from ``_run()``.
        """
