"""Generic pydantic-ai BaseAgent with composable middleware."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for _takes_ctx annotation inspection
from pydantic_ai.messages import (
    ModelMessage,  # noqa: TC002 — needed at runtime for pydantic-ai history_processor introspection
)
from pydantic_ai.usage import RunUsage, UsageLimits

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.tools import ToolDefinition

    from libs.pydantic_agent._middleware import AgentMiddleware

DepsT = TypeVar("DepsT")


class BaseAgent(Generic[DepsT]):
    """Generic base class that runs a pydantic-ai agent with a composable middleware chain.

    Subclasses must set ``self._agent`` in their ``__init__`` before calling ``_arun``.

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
        self.context_window_token_usage: int = 0
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

        def _chained_history_processor(ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
            for m in middleware:
                messages = m.before_model_req_edit_messages(ctx, messages)
            return messages

        async def _chained_prepare_tools(
            ctx: RunContext[Any], tool_defs: list[ToolDefinition]
        ) -> list[ToolDefinition] | None:
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
        """Synchronous wrapper around ``_arun``."""
        return asyncio.run(self._arun(prompt, _run_ctx=_run_ctx, **kwargs))

    async def _arun(
        self,
        prompt: str,
        *,
        _run_ctx: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run the agent, invoke middleware hooks, and accumulate usage.

        Uses ``agent.iter()`` for node-by-node execution so that on a
        retryable error the accumulated message history (including completed
        tool calls) is captured and the run resumes from where it left off.
        """

        self.current_run_usage = RunUsage()
        self.context_window_token_usage = 0
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
                                        self.context_window_token_usage = (
                                            stream.usage().input_tokens or 0  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
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

        # Only fall back to cumulative usage when streaming didn't provide
        # per-request tokens (e.g. the model backend lacks stream.usage()).
        # The streaming loop sets context_window_token_usage to the *last*
        # individual request's input tokens — the actual context size, not the
        # sum across all requests in the run.
        if self.context_window_token_usage == 0:  # pyright: ignore[reportUnknownMemberType]
            self.context_window_token_usage = result.usage().input_tokens or 0
        for m in self._middleware:
            m.after_run(result, _run_ctx)
        return result
