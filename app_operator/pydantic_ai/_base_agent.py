"""Base class for Pydantic AI operator agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai.usage import UsageLimits

if TYPE_CHECKING:
    from app_operator.pydantic_ai._deps import OperatorDeps
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class BaseAgent:
    """Base class that atomically runs an agent, records the trajectory, and accumulates usage.

    Subclasses must set ``self._agent`` in their ``__init__`` before calling ``_run``.
    Subclasses may set class attributes ``phase`` and ``agent_name`` for fixed values.
    """

    phase: str | None = None
    agent_name: str | None = None

    def __init__(self, deps: OperatorDeps, recorder: PydanticAITrajectoryRecorder) -> None:
        self.deps = deps
        self.recorder = recorder
        self._usage_limits = UsageLimits()

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

        result = self._agent.run_sync(
            prompt,
            deps=self.deps,
            usage_limits=self._usage_limits,
            **kwargs,
        )
        self.recorder.record_run(resolved_phase, resolved_agent_name, result, context=context)
        return result
