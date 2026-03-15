"""Generic pydantic-ai BaseAgent with composable middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic_ai.usage import RunUsage, UsageLimits

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from libs.pydantic_agent._middleware import AgentMiddleware

DepsT = TypeVar("DepsT")


class BaseAgent(Generic[DepsT]):
    """Generic base class that runs a pydantic-ai agent with a composable middleware chain.

    Subclasses must set ``self._agent`` in their ``__init__`` before calling ``_run``.

    Args:
        deps: Dependency injection object passed to ``agent.run_sync()``.
        agent_name: Human-readable name for this agent instance (used in logging, trajectories).
        middleware: Optional list of ``AgentMiddleware`` instances.
            ``on_stream_event`` and ``after_run`` are called in registration order.
            ``on_attach`` is called for each middleware during ``__init__``.
    """

    # Declared for type checkers; concrete subclasses assign this in __init__.
    _agent: Agent[DepsT, Any]

    def __init__(
        self,
        deps: DepsT,
        *,
        agent_name: str,
        middleware: list[AgentMiddleware] | None = None,
    ) -> None:
        self.deps = deps
        self._agent_name = agent_name
        self._usage_limits = UsageLimits()
        self._middleware: list[AgentMiddleware] = middleware or []
        self.current_run_usage: RunUsage = RunUsage()
        for m in self._middleware:
            m.on_attach(self)

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def _stream_event_chain(self, event: Any) -> None:
        """Dispatch a streaming event to all middleware in order."""
        for m in self._middleware:
            m.on_stream_event(event)

    def _run(
        self,
        prompt: str,
        *,
        _run_ctx: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run the agent, invoke middleware hooks, and accumulate usage.

        Always use this instead of calling ``self._agent.run_sync()`` directly.

        Args:
            prompt: The user prompt to pass to the agent.
            _run_ctx: Optional context dict forwarded to ``after_run`` on each middleware.
            **kwargs: Additional keyword arguments forwarded to ``run_sync()``.
        """

        self.current_run_usage = RunUsage()
        for m in self._middleware:
            m.before_run()

        async def _stream_handler(ctx: Any, events: Any) -> None:
            async for event in events:
                self.current_run_usage = ctx.usage
                self._stream_event_chain(event)

        result = self._agent.run_sync(
            prompt,
            deps=self.deps,
            usage_limits=self._usage_limits,
            event_stream_handler=_stream_handler,
            **kwargs,
        )
        for m in self._middleware:
            m.after_run(result, _run_ctx)
        return result
