"""AgentMiddleware base class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from libs.pydantic_agent._base import BaseAgent


class AgentMiddleware:
    """Base class for BaseAgent tool call middleware.

    After registration, ``self._agent`` is set to the owning ``BaseAgent`` instance
    via ``on_attach``. All hook methods may access ``self._agent`` and any of its
    public attributes (e.g. ``agent_name``).

    Hook call order:
    - ``on_attach``: called once when middleware is registered with an agent.
    - ``before_tool_call`` / ``after_tool_call``: called around each tool invocation.
      ``before_tool_call`` in registration order; ``after_tool_call`` in reverse.
    - ``after_run``: called once per ``_run()`` after ``run_sync()`` completes, in registration order.
    """

    #: Set by ``on_attach`` to the owning ``BaseAgent`` instance.
    _agent: BaseAgent

    def on_attach(self, agent: BaseAgent) -> None:
        """Called once when this middleware is attached to an agent.
        Stores the agent as ``self._agent``. Override to perform additional setup.
        """
        self._agent = agent

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
