"""PydanticAIDriver — ``AgentDriver`` implementation backed by pydantic-ai's ``BaseAgent``.

Always uses ``BaseAgent`` (via ``InlineAgent``) internally.  Handles
middleware assembly per agent-name pattern, interrupt exception catching,
context compaction for SRE agents, and trajectory recording.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded

from libs.agent_mw import (
    FixedPathProvider,
    LoopDetectionMiddleware,
    RetryMiddleware,
    SoftLimitExtension,
    StallDetectionMiddleware,
    ThinkingRepetitionMiddleware,
    TimeoutMiddleware,
    TrajectoryMiddleware,
    TurnLoggingMiddleware,
)
from libs.pydantic_agent import AgentMiddleware, InlineAgent
from sregym_agents.crucible.agents.base import AgentDriver, AgentResult

if TYPE_CHECKING:
    from pathlib import Path

    from libs.pydantic_agent import UsageCollector

T = TypeVar("T")
logger = logging.getLogger(__name__)


def _middleware_for_agent(
    agent_name: str,
    trajectory_path: Path | None = None,
    step_limit: int | None = 500,
) -> list[AgentMiddleware]:
    """Build the middleware stack appropriate for *agent_name*.

    Naming conventions:
    - ``sre-*``  → heavy (TurnLogging, Retry, ThinkingRepetition,
      LoopDetection, StallDetection, Timeout, SoftLimit)
    - ``judge-*`` → medium (TurnLogging, Retry, LoopDetection,
      Timeout, SoftLimit)
    - anything else → light (TurnLogging, Retry)
    """
    mw: list[AgentMiddleware]

    if agent_name.startswith(("sre-", "recovery-")):
        mw = [
            TurnLoggingMiddleware(),
            RetryMiddleware(),
            ThinkingRepetitionMiddleware(),
            LoopDetectionMiddleware(),
            StallDetectionMiddleware(),
            TimeoutMiddleware(),
            SoftLimitExtension(step_limit),
        ]
    elif agent_name.startswith("judge-"):
        mw = [
            TurnLoggingMiddleware(),
            RetryMiddleware(),
            LoopDetectionMiddleware(),
            TimeoutMiddleware(),
            SoftLimitExtension(step_limit),
        ]
    else:
        mw = [
            TurnLoggingMiddleware(),
            RetryMiddleware(),
        ]
        if trajectory_path is not None:
            mw.append(TrajectoryMiddleware(FixedPathProvider(trajectory_path)))
        return mw

    if trajectory_path is not None:
        mw.insert(0, TrajectoryMiddleware(FixedPathProvider(trajectory_path)))
    return mw


# ---------------------------------------------------------------------------
# Context window helpers (duplicated from sre_agent.py so the driver is
# self-contained; will replace the original in Phase 2).
# ---------------------------------------------------------------------------

_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gemini": 1_000_000,
}

CONTEXT_COMPACT_THRESHOLD = 0.80


def _context_window_for(model: Any) -> int:
    from pydantic_ai.models import Model

    name = model.model_name if isinstance(model, Model) else str(model)
    for prefix, window in _CONTEXT_WINDOWS.items():
        if prefix in name:
            return window
    return 128_000


async def _compact_messages(
    model: Any,
    messages: list[Any],
    usage_collector: UsageCollector | None = None,
) -> str:
    """Summarize message history for context compaction."""
    import json

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    from libs.agent_mw import arun_with_retry
    from libs.pydantic_agent import TokenUsage

    to_summarize: list[Any] = messages[1:] if len(messages) > 1 else messages
    try:
        raw = json.loads(ModelMessagesTypeAdapter.dump_json(to_summarize))
        parts: list[str] = []
        for msg in raw:
            kind = msg.get("kind", "unknown")
            for part in msg.get("parts", []):
                part_kind = part.get("part_kind", "")
                content = part.get("content", "")
                if isinstance(content, str) and content:
                    parts.append(f"[{kind}/{part_kind}]: {content}")
        history_text = "\n\n".join(parts)
    except Exception as exc:
        logger.warning(f"Message serialization failed: {exc}")
        history_text = str(to_summarize)

    summary_prompt = (
        "Summarize the following conversation history, preserving all key findings, "
        "actions taken, commands run, outputs observed, hypotheses formed, and current "
        "state. Be detailed enough for the agent to continue without losing context.\n\n"
        f"<history>\n{history_text}\n</history>"
    )
    compactor: Agent[None, str] = Agent(model, output_type=str)
    compact_result = await arun_with_retry(compactor, summary_prompt)
    if usage_collector is not None:
        usage_collector.add("compact-messages", TokenUsage.from_run_usage(compact_result.usage()))
    logger.info(f"Context compacted: {len(history_text)} chars → {len(compact_result.output)} chars")
    return compact_result.output


class PydanticAIDriver(AgentDriver):
    """``AgentDriver`` backed by pydantic-ai's ``BaseAgent`` / ``InlineAgent``.

    Constructor parameters:
        model: pydantic-ai model instance or model string.
        trajectory_path: Optional path for trajectory JSONL recording.
        interrupt_exceptions: Tuple of exception types that represent
            short-circuit interrupts. When raised during a run, they are
            caught and returned as ``AgentResult(completed=False,
            interrupt_data=exc)``.
        step_limit: Soft step limit for SRE/judge middleware stacks.
    """

    def __init__(
        self,
        model: Any,
        *,
        trajectory_path: Path | None = None,
        interrupt_exceptions: tuple[type[BaseException], ...] = (),
        step_limit: int | None = 500,
    ) -> None:
        self._model = model
        self._trajectory_path = trajectory_path
        self._interrupt_exceptions = interrupt_exceptions
        self._step_limit = step_limit

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        output_type: type[T] = str,  # type: ignore[assignment]
        agent_name: str = "",
        timeout: int | None = None,
        model_settings: dict[str, Any] | None = None,
        message_history: list[Any] | None = None,
        usage_collector: Any | None = None,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Run a pydantic-ai agent and return an ``AgentResult``.

        Backend-specific kwargs:
            deps: Dependency object passed to ``BaseAgent``.  When provided,
                the agent is ``BaseAgent[type(deps)]``; otherwise
                ``BaseAgent[None]`` (via ``InlineAgent``).
            run_ctx: Run context dict passed to ``BaseAgent._arun()``.
        """
        deps = kwargs.pop("deps", None)
        run_ctx: dict[str, Any] | None = kwargs.pop("run_ctx", None)

        middleware = _middleware_for_agent(
            agent_name,
            trajectory_path=self._trajectory_path,
            step_limit=self._step_limit,
        )

        agent = InlineAgent(
            self._model,
            agent_name=agent_name,
            output_type=output_type,
            tools=tools or [],
            model_settings=model_settings,
            middleware=middleware,
            usage_collector=usage_collector,
        )

        if system_prompt and agent.agent:

            @agent.agent.instructions
            def _system_instructions() -> str:  # pyright: ignore[reportUnusedFunction]
                return system_prompt

        # If deps provided, set them on the underlying agent so tools can
        # access ctx.deps.  InlineAgent sets deps=None by default.
        if deps is not None:
            agent.deps = deps
            agent._agent._deps_type = type(deps)  # pyright: ignore[reportPrivateUsage]

        enable_compaction = agent_name.startswith(("sre-", "recovery-"))
        context_window = _context_window_for(self._model) if enable_compaction else 0
        initial_prompt = prompt
        current_prompt = prompt
        arun_kwargs: dict[str, Any] = {}
        if message_history is not None:
            arun_kwargs["message_history"] = message_history

        result_output: T | None = None
        result_messages: list[Any] = []

        while True:
            try:
                result = await agent.arun(current_prompt, run_ctx=run_ctx, **arun_kwargs)
            except tuple(self._interrupt_exceptions) as exc:  # type: ignore[misc]
                return AgentResult(
                    completed=False,
                    interrupt_data=exc,
                )
            except ModelHTTPError:
                return AgentResult(completed=False)
            except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
                logger.warning(f"Model returned unexpected output; treating as unsubmitted. ({exc})")
                return AgentResult(completed=False, output=None)

            result_output = result.output
            result_messages = list(result.all_messages())

            # Context compaction for SRE agents
            if enable_compaction and context_window > 0:
                last_token_count = agent.context_window_token_usage
                if last_token_count > CONTEXT_COMPACT_THRESHOLD * context_window:
                    logger.warning(
                        f"Context approaching limit ({last_token_count} > "
                        f"{CONTEXT_COMPACT_THRESHOLD * context_window:.0f}). Compacting..."
                    )
                    summary = await _compact_messages(
                        self._model, result.all_messages(), usage_collector=usage_collector
                    )
                    current_prompt = initial_prompt + "\n\nHere's a summary of the previous conversation:\n\n" + summary
                    # Reset message history for fresh start with summary
                    arun_kwargs.pop("message_history", None)
                    logger.warning("Context compacted. Restarting with summary.")
                    continue

            break

        return AgentResult(
            output=result_output,
            completed=True,
            messages=result_messages,
        )
