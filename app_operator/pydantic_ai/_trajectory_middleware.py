"""Middleware that records agent runs to a PydanticAITrajectoryRecorder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from libs.pydantic_agent import AgentMiddleware

if TYPE_CHECKING:
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class TrajectoryMiddleware(AgentMiddleware):
    """Records each agent run via ``PydanticAITrajectoryRecorder``.

    Expects ``run_ctx`` to contain ``"phase"``, ``"agent_name"``, and optionally ``"context"``.
    """

    def __init__(self, recorder: PydanticAITrajectoryRecorder) -> None:
        self._recorder = recorder

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        if run_ctx is None:
            return
        self._recorder.record_run(
            run_ctx["phase"],
            run_ctx["agent_name"],
            result,
            context=run_ctx.get("context"),
        )
