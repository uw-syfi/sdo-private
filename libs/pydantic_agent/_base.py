"""Generic pydantic-ai BaseAgent with composable middleware."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for _takes_ctx annotation inspection
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
        self.current_request_input_tokens: int = 0
        for m in self._middleware:
            m.on_attach(self)

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def _build_agent(self, *args: Any, **kwargs: Any) -> Agent[DepsT, Any]:
        """Construct a pydantic-ai Agent, wiring in history_processors and
        prepare_tools collected from all registered middleware."""
        from pydantic_ai import Agent

        middleware = self._middleware

        def _chained_history_processor(ctx: RunContext[Any], messages: list) -> list:
            for m in middleware:
                messages = m.before_model_req_edit_messages(ctx, messages)
            return messages

        async def _chained_prepare_tools(ctx: RunContext[Any], tool_defs: list) -> list | None:
            for m in middleware:
                result = await m.before_model_req_edit_tools(ctx, tool_defs)
                if result is not None:
                    tool_defs = result
            return tool_defs

        kwargs.setdefault("history_processors", []).append(_chained_history_processor)
        kwargs["prepare_tools"] = _chained_prepare_tools
        return Agent(*args, **kwargs)

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
        self.current_request_input_tokens = 0
        for m in self._middleware:
            m.before_run()

        async def _stream_handler(ctx: Any, events: Any) -> None:
            async for event in events:
                self.current_run_usage = ctx.usage
                self._stream_event_chain(event)

        while True:
            try:
                result = self._agent.run_sync(
                    prompt,
                    deps=self.deps,
                    usage_limits=self._usage_limits,
                    event_stream_handler=_stream_handler,
                    **kwargs,
                )
                break
            except Exception as exc:
                delay: float | None = None
                for m in self._middleware:
                    delay = m.on_run_error(exc)
                    if delay is not None:
                        break
                if delay is None:
                    raise
                time.sleep(delay)

        self.current_request_input_tokens = result.usage().input_tokens or 0
        for m in self._middleware:
            m.after_run(result, _run_ctx)
        return result

    async def _arun(
        self,
        prompt: str,
        *,
        _run_ctx: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Async counterpart of ``_run``.

        Uses ``agent.iter()`` for node-by-node execution so that on a
        retryable error the accumulated message history (including completed
        tool calls) is captured and the run resumes from where it left off.
        """

        self.current_run_usage = RunUsage()
        self.current_request_input_tokens = 0
        for m in self._middleware:
            m.before_run()

        message_history = kwargs.pop("message_history", None)
        current_prompt: str | None = prompt

        while True:
            agent_run = None
            try:
                async with self._agent.iter(
                    current_prompt,
                    deps=self.deps,
                    usage_limits=self._usage_limits,
                    message_history=message_history,
                    **kwargs,
                ) as agent_run:
                    async for node in agent_run:
                        if self._agent.is_model_request_node(node) or self._agent.is_call_tools_node(node):
                            async with node.stream(agent_run.ctx) as stream:
                                ctx_baseline_tokens = agent_run.ctx.state.usage.input_tokens or 0
                                async for event in stream:
                                    self.current_run_usage = agent_run.ctx.state.usage
                                    if hasattr(stream, "usage"):
                                        self.current_request_input_tokens = (
                                            stream.usage().input_tokens or 0  # pyright: ignore[reportAttributeAccessIssue]
                                        ) - ctx_baseline_tokens
                                    self._stream_event_chain(event)

                result = agent_run.result
                assert result is not None
                break
            except Exception as exc:
                delay: float | None = None
                for m in self._middleware:
                    delay = m.on_run_error(exc)
                    if delay is not None:
                        break
                if delay is None:
                    raise
                # Capture accumulated messages for resume if available
                if agent_run is not None:
                    message_history = list(agent_run.all_messages())
                    current_prompt = None
                await asyncio.sleep(delay)

        self.current_request_input_tokens = result.usage().input_tokens or 0
        for m in self._middleware:
            m.after_run(result, _run_ctx)
        return result
