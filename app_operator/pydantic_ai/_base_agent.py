"""Base class for Pydantic AI operator agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_ai.usage import UsageLimits

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai._run_context import RunContext
    from pydantic_ai.toolsets.abstract import ToolsetTool

    from app_operator.pydantic_ai._deps import OperatorDeps
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class AgentMiddleware:
    """Base class for BaseAgent tool call middleware.

    Override before_tool_call and/or after_tool_call to observe or control tool execution.
    before_tool_call is called in registration order; after_tool_call in reverse (wrapper semantics).
    """

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Called before each tool call. Return False to reject. Default: allow."""
        return True

    def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Called after each tool call completes. Default: no-op."""


@dataclass
class _InterceptingToolset(WrapperToolset):
    """Wraps a toolset to call before/after callbacks around every tool call.

    Must be a dataclass so pydantic_ai's internal ``dataclasses.replace()`` call
    (inside ``visit_and_replace``) can reconstruct it with all fields intact.
    """

    before_cb: Callable[[str, dict[str, Any]], bool]
    after_cb: Callable[[str, dict[str, Any], Any], None]

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext, tool: ToolsetTool) -> Any:
        if not self.before_cb(name, tool_args):
            raise ModelRetry("Tool call rejected by middleware")
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        self.after_cb(name, tool_args, result)
        return result


class BaseAgent:
    """Base class that atomically runs an agent, records the trajectory, and accumulates usage.

    Subclasses must set ``self._agent`` in their ``__init__`` before calling ``_run``.
    Subclasses may set class attributes ``phase`` and ``agent_name`` for fixed values.

    Args:
        middleware: Optional list of AgentMiddleware instances. before_tool_call is called
            in list order; after_tool_call in reverse (outermost middleware wraps innermost).
    """

    phase: str | None = None
    agent_name: str | None = None

    def __init__(
        self,
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
        *,
        middleware: list[AgentMiddleware] | None = None,
    ) -> None:
        self.deps = deps
        self.recorder = recorder
        self._usage_limits = UsageLimits()
        self._middleware: list[AgentMiddleware] = middleware or []

    def _before_chain(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Call before_tool_call on each middleware in order. Short-circuits on False."""
        return all(m.before_tool_call(tool_name, args) for m in self._middleware)

    def _after_chain(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Call after_tool_call on each middleware in reverse order."""
        for m in reversed(self._middleware):
            m.after_tool_call(tool_name, args, result)

    def _run(
        self,
        prompt: str,
        *,
        phase: str | None = None,
        agent_name: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call run_sync, record_run, and accumulate usage atomically.

        Always use this instead of calling ``self._agent.run_sync()`` directly.
        phase and agent_name default to class attributes when not passed.
        """
        resolved_phase = phase if phase is not None else self.phase
        resolved_agent_name = agent_name if agent_name is not None else self.agent_name
        if resolved_phase is None or resolved_agent_name is None:
            raise ValueError("phase and agent_name must be set (via class attr or argument)")

        hooked = [
            _InterceptingToolset(
                wrapped=ts,
                before_cb=self._before_chain,
                after_cb=self._after_chain,
            )
            for ts in self._agent.toolsets
        ]
        with self._agent.override(tools=[], toolsets=hooked):
            result = self._agent.run_sync(
                prompt,
                deps=self.deps,
                usage_limits=self._usage_limits,
                **kwargs,
            )
        self.recorder.record_run(resolved_phase, resolved_agent_name, result, context=context)
        return result
