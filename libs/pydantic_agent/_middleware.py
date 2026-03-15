"""AgentMiddleware base class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai.messages import (
        BuiltinToolCallEvent,
        BuiltinToolResultEvent,
        FinalResultEvent,
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartDeltaEvent,
        PartEndEvent,
        PartStartEvent,
    )

    from libs.pydantic_agent._base import BaseAgent


class AgentMiddleware:
    """Base class for BaseAgent tool call middleware.

    After registration, ``self._agent`` is set to the owning ``BaseAgent`` instance
    via ``on_attach``. All hook methods may access ``self._agent`` and any of its
    public attributes (e.g. ``agent_name``).

    Hook call order:
    - ``on_attach``: called once when middleware is registered with an agent.
    - ``on_stream_event`` (and its typed delegates): called once per streaming event
      during ``run_sync()``, in registration order.
    - ``after_run``: called once per ``_run()`` after ``run_sync()`` completes, in registration order.

    Streaming hooks (all no-ops by default):
    - ``on_part_start``     — a new model-response part (text/thinking/tool-call) begins.
    - ``on_part_delta``     — incremental content for an in-progress part.
    - ``on_part_end``       — a model-response part completes (full part available).
    - ``on_final_result``   — the agent's final result/output is ready.
    - ``on_function_tool_call``   — a function tool is about to be invoked.
    - ``on_function_tool_result`` — a function tool returned a result.
    - ``on_builtin_tool_call``    — a builtin tool is about to be invoked.
    - ``on_builtin_tool_result``  — a builtin tool returned a result.

    Override ``on_stream_event`` directly for a catch-all, or override the typed
    hooks above for specific event types.  The default ``on_stream_event``
    implementation dispatches to the typed hooks.
    """

    #: Set by ``on_attach`` to the owning ``BaseAgent`` instance.
    _agent: BaseAgent

    def on_attach(self, agent: BaseAgent) -> None:
        """Called once when this middleware is attached to an agent.
        Stores the agent as ``self._agent``. Override to perform additional setup.
        """
        self._agent = agent

    def before_run(self) -> None:
        """Called once immediately before each ``_run()`` call."""

    # ------------------------------------------------------------------
    # Streaming hooks
    # ------------------------------------------------------------------

    def on_stream_event(self, event: Any) -> None:
        """Dispatches each streaming event to the appropriate typed hook.

        Override this directly for a catch-all handler, or override the
        individual typed hooks below instead.
        """
        from pydantic_ai.messages import (
            BuiltinToolCallEvent,
            BuiltinToolResultEvent,
            FinalResultEvent,
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            PartDeltaEvent,
            PartEndEvent,
            PartStartEvent,
        )

        if isinstance(event, PartStartEvent):
            self.on_part_start(event)
        elif isinstance(event, PartDeltaEvent):
            self.on_part_delta(event)
        elif isinstance(event, PartEndEvent):
            self.on_part_end(event)
        elif isinstance(event, FinalResultEvent):
            self.on_final_result(event)
        elif isinstance(event, FunctionToolCallEvent):
            self.on_function_tool_call(event)
        elif isinstance(event, FunctionToolResultEvent):
            self.on_function_tool_result(event)
        elif isinstance(event, BuiltinToolCallEvent):
            self.on_builtin_tool_call(event)
        elif isinstance(event, BuiltinToolResultEvent):
            self.on_builtin_tool_result(event)

    def on_part_start(self, event: PartStartEvent) -> None:
        """A new model-response part (text, thinking, tool-call, …) has started."""

    def on_part_delta(self, event: PartDeltaEvent) -> None:
        """Incremental content arrived for an in-progress model-response part."""

    def on_part_end(self, event: PartEndEvent) -> None:
        """A model-response part completed; ``event.part`` holds the full content."""

    def on_final_result(self, event: FinalResultEvent) -> None:
        """The agent's final result/output is ready."""

    def on_function_tool_call(self, event: FunctionToolCallEvent) -> None:
        """A function tool is about to be invoked."""

    def on_function_tool_result(self, event: FunctionToolResultEvent) -> None:
        """A function tool returned a result."""

    def on_builtin_tool_call(self, event: BuiltinToolCallEvent) -> None:
        """A builtin tool is about to be invoked."""

    def on_builtin_tool_result(self, event: BuiltinToolResultEvent) -> None:
        """A builtin tool returned a result."""

    # ------------------------------------------------------------------

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        """Called once after each agent run completes.

        Args:
            result: The ``RunResult`` returned by ``agent.run_sync()``.
            run_ctx: Optional context dict passed through from ``_run()``.
        """
