"""Operator-specific agent base with trajectory recording wired in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app_operator.pydantic_ai._console_logging import ConsoleLoggingMiddleware
from app_operator.pydantic_ai._trajectory_middleware import TrajectoryMiddleware
from libs.pydantic_agent import AgentMiddleware, BaseAgent

if TYPE_CHECKING:
    from app_operator.pydantic_ai._deps import OperatorDeps
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder
    from app_operator.trajectory import Phase

__all__ = ["OperatorAgent"]


class OperatorAgent(BaseAgent["OperatorDeps"]):
    """Base class for all operator agents.

    Automatically prepends :class:`TrajectoryMiddleware` and :class:`ConsoleLoggingMiddleware`
    so every ``_run()`` call is recorded and logged.  Subclasses set ``phase`` as a class
    attribute (or pass it per-call).

    Args:
        deps: Operator dependency container.
        recorder: Trajectory recorder used by :class:`TrajectoryMiddleware`.
        agent_name: Human-readable name for this agent (required).
        middleware: Additional middleware appended after built-in middleware.
    """

    phase: Phase | None = None

    def __init__(
        self,
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
        *,
        agent_name: str,
        middleware: list[AgentMiddleware] | None = None,
    ) -> None:
        trajectory_mw = TrajectoryMiddleware(recorder)
        console_mw = ConsoleLoggingMiddleware()
        all_middleware = [trajectory_mw, console_mw] + (middleware or [])
        super().__init__(deps, agent_name=agent_name, middleware=all_middleware)

    def _run(
        self,
        prompt: str,
        *,
        phase: Phase | None = None,
        agent_name: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run the agent and record the trajectory.

        ``phase`` defaults to the class-level attribute when not passed.
        ``agent_name`` overrides ``self.agent_name`` for this call only (e.g. for
        per-call agent name variants); defaults to ``self.agent_name``.
        """
        resolved_phase = phase if phase is not None else self.phase
        resolved_agent_name = agent_name if agent_name is not None else self.agent_name
        if resolved_phase is None:
            raise ValueError("phase must be set (via class attr or argument)")

        _run_ctx = {"phase": resolved_phase, "agent_name": resolved_agent_name, "context": context}
        return super()._run(prompt, _run_ctx=_run_ctx, **kwargs)
