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
    """

    def __init__(self, deps: OperatorDeps, recorder: PydanticAITrajectoryRecorder) -> None:
        self.deps = deps
        self.recorder = recorder

    def _run(
        self,
        prompt: str,
        phase: str,
        agent_name: str,
        *,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call run_sync, record_run, and accumulate usage atomically.

        Always use this instead of calling ``self._agent.run_sync()`` directly.
        """
        result = self._agent.run_sync(
            prompt,
            deps=self.deps,
            usage_limits=UsageLimits(),
            **kwargs,
        )
        self.recorder.record_run(phase, agent_name, result, context=context)
        return result
