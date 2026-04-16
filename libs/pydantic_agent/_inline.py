"""Lightweight BaseAgent wrapper for single-shot inline subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from libs.pydantic_agent._base import BaseAgent

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

    from libs.pydantic_agent._middleware import AgentMiddleware
    from libs.pydantic_agent._usage import UsageCollector

DepsT = TypeVar("DepsT")
OutputT = TypeVar("OutputT")


class InlineAgent(BaseAgent[DepsT], Generic[DepsT, OutputT]):
    """A reusable BaseAgent wrapper for ephemeral subagents that need middleware.

    Wraps a plain pydantic-ai Agent in BaseAgent so that middleware like
    TurnLoggingMiddleware, RetryMiddleware, and TrajectoryMiddleware work
    without boilerplate.

    Usage::

        agent = InlineAgent(
            model,
            agent_name="triage",
            output_type=TriageReport,
            tools=[read_file, exec_bash],
            model_settings=thinking_settings(model, 4096),
            middleware=[TurnLoggingMiddleware(), RetryMiddleware()],
        )
        result = await agent.arun(prompt, run_ctx={"stage": "diagnosis"})
    """

    def __init__(
        self,
        model: Model | str,
        *,
        deps: DepsT,
        agent_name: str,
        output_type: type[OutputT],
        tools: list[Any] | None = None,
        model_settings: Any | None = None,
        middleware: list[AgentMiddleware] | None = None,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        super().__init__(
            deps,
            agent_name=agent_name,
            middleware=middleware,
            usage_collector=usage_collector,
        )
        self._agent = self._build_agent(
            model,
            output_type=output_type,
            tools=tools or [],
            model_settings=model_settings,
            deps_type=type(deps) if deps is not None else type(None),
        )

    @property
    def agent(self) -> Agent[DepsT, OutputT]:
        """The underlying pydantic-ai Agent (e.g. for registering output validators)."""
        return self._agent

    async def arun(
        self,
        prompt: str,
        *,
        run_ctx: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        return await self._arun(prompt, _run_ctx=run_ctx, **kwargs)
