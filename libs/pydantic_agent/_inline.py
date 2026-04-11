"""Lightweight BaseAgent wrapper for single-shot inline subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from libs.pydantic_agent._base import BaseAgent

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

    from libs.pydantic_agent._middleware import AgentMiddleware
    from libs.pydantic_agent._usage import UsageCollector

OutputT = TypeVar("OutputT")


class InlineAgent(BaseAgent[None], Generic[OutputT]):
    """A reusable BaseAgent[None] for ephemeral subagents that need middleware.

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
        agent_name: str,
        output_type: type[OutputT],
        tools: list[Any] | None = None,
        model_settings: Any | None = None,
        middleware: list[AgentMiddleware] | None = None,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        super().__init__(
            None,
            agent_name=agent_name,
            middleware=middleware,
            usage_collector=usage_collector,
        )
        self._agent = self._build_agent(
            model,
            output_type=output_type,
            tools=tools or [],
            model_settings=model_settings,
        )

    @property
    def agent(self) -> Agent[None, OutputT]:
        """The underlying pydantic-ai Agent (e.g. for registering output validators)."""
        return self._agent

    def set_deps(self, deps: Any) -> None:
        """Set deps and deps_type on the underlying agent.

        This keeps private-attribute access contained within InlineAgent
        rather than forcing callers to reach into ``_agent._deps_type``.
        """
        self.deps = deps
        self._agent._deps_type = type(deps)  # pyright: ignore[reportPrivateUsage]

    async def arun(
        self,
        prompt: str,
        *,
        run_ctx: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        return await self._arun(prompt, _run_ctx=run_ctx, **kwargs)
