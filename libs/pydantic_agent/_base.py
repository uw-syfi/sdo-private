"""Generic pydantic-ai BaseAgent with composable middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_ai.usage import UsageLimits

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai import Agent
    from pydantic_ai._run_context import RunContext
    from pydantic_ai.toolsets.abstract import ToolsetTool

    from libs.pydantic_agent._middleware import AgentMiddleware

DepsT = TypeVar("DepsT")


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
        result = None
        try:
            result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
            return result
        except Exception:
            raise
        finally:
            self.after_cb(name, tool_args, result)


class BaseAgent(Generic[DepsT]):
    """Generic base class that runs a pydantic-ai agent with a composable middleware chain.

    Subclasses must set ``self._agent`` in their ``__init__`` before calling ``_run``.

    Args:
        deps: Dependency injection object passed to ``agent.run_sync()``.
        middleware: Optional list of ``AgentMiddleware`` instances.
            ``before_tool_call`` is called in list order; ``after_tool_call`` in reverse
            (outermost middleware wraps innermost). ``after_run`` is called in list order.
    """

    # Declared for type checkers; concrete subclasses assign this in __init__.
    _agent: Agent[DepsT, Any]

    def __init__(
        self,
        deps: DepsT,
        *,
        middleware: list[AgentMiddleware] | None = None,
    ) -> None:
        self.deps = deps
        self._usage_limits = UsageLimits()
        self._middleware: list[AgentMiddleware] = middleware or []

    def _before_chain(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Call ``before_tool_call`` on each middleware in order. Short-circuits on False."""
        return all(m.before_tool_call(tool_name, args) for m in self._middleware)

    def _after_chain(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Call ``after_tool_call`` on each middleware in reverse order."""
        for m in reversed(self._middleware):
            m.after_tool_call(tool_name, args, result)

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
        # agent.toolsets is a public property documented to include "a function toolset
        # holding tools that were registered on the agent directly" (i.e. tools=[...]).
        # Wrap every toolset so all tool calls go through the middleware chain.
        hooked = [
            _InterceptingToolset(
                wrapped=ts,  # type: ignore[arg-type]  # WrapperToolset is typed for None deps
                before_cb=self._before_chain,
                after_cb=self._after_chain,
            )
            for ts in self._agent.toolsets
        ]
        # tools=[] clears the original function toolset so it doesn't also run
        # unwrapped alongside the intercepted copy already captured in `hooked`.
        # toolsets=hooked replaces user toolsets with the wrapped versions.
        with self._agent.override(tools=[], toolsets=hooked):  # type: ignore[arg-type]
            result = self._agent.run_sync(
                prompt,
                deps=self.deps,
                usage_limits=self._usage_limits,
                **kwargs,
            )
        for m in self._middleware:
            m.after_run(result, _run_ctx)
        return result
