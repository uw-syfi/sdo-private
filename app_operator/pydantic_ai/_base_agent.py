"""Operator-specific agent base with trajectory recording wired in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app_operator.pydantic_ai._trajectory_middleware import TrajectoryMiddleware
from libs.pydantic_agent import AgentMiddleware, BaseAgent

if TYPE_CHECKING:
    from app_operator.pydantic_ai._deps import OperatorDeps
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder
    from app_operator.trajectory import Phase

__all__ = ["OperatorAgent"]


class OperatorAgent(BaseAgent["OperatorDeps"]):
    """Base class for all operator agents.

    Automatically prepends :class:`TrajectoryMiddleware` so every ``_run()`` call
    is recorded.  Subclasses set ``phase`` and ``agent_name`` as class attributes
    (or pass them per-call).

    Args:
        deps: Operator dependency container.
        recorder: Trajectory recorder used by :class:`TrajectoryMiddleware`.
        middleware: Additional middleware prepended *after* ``TrajectoryMiddleware``.
    """

    phase: Phase | None = None
    agent_name: str | None = None

    def __init__(
        self,
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
        *,
        middleware: list[AgentMiddleware] | None = None,
    ) -> None:
        trajectory_mw = TrajectoryMiddleware(recorder)
        all_middleware = [trajectory_mw] + (middleware or [])
        super().__init__(deps, middleware=all_middleware)

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

        ``phase`` and ``agent_name`` default to class-level attributes when not passed.
        """
        resolved_phase = phase if phase is not None else self.phase
        resolved_agent_name = agent_name if agent_name is not None else self.agent_name
        if resolved_phase is None or resolved_agent_name is None:
            raise ValueError("phase and agent_name must be set (via class attr or argument)")

        _run_ctx = {"phase": resolved_phase, "agent_name": resolved_agent_name, "context": context}
        return super()._run(prompt, _run_ctx=_run_ctx, **kwargs)
