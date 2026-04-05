"""CrucibleJudgeAgent: pydantic-ai judge agent for the dual-agent judge loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai import Agent

from pydantic_ai.exceptions import UnexpectedModelBehavior

from libs.agent_mw import (
    FixedPathProvider,
    LoopDetectionMiddleware,
    RetryMiddleware,
    SoftLimitExtension,
    TimeoutMiddleware,
    TrajectoryMiddleware,
    TurnLoggingMiddleware,
)
from libs.pydantic_agent import thinking_settings
from libs.pydantic_agent._base import BaseAgent
from sregym_agents.crucible._prompts import _render
from sregym_agents.crucible.tools import (
    THINKING_BUDGET,
    JudgeDeps,
    exec_bash_any,
    grep,
    read_file,
    reveal_agent_hypothesis,
    str_replace_file,
    submit_independent_findings,
    submit_verdict,
    write_file,
)

logger = logging.getLogger(__name__)


class CrucibleJudgeAgent(BaseAgent[JudgeDeps]):
    MAX_SUBMIT_REMINDERS = 3

    def __init__(
        self, model: str, deps: JudgeDeps, trajectory_path: Path | None = None, step_limit: int | None = 500
    ) -> None:
        mw = [
            TurnLoggingMiddleware(),
            RetryMiddleware(),
            LoopDetectionMiddleware(),
            TimeoutMiddleware(),
            SoftLimitExtension(step_limit),
        ]
        if trajectory_path is not None:
            mw.insert(0, TrajectoryMiddleware(FixedPathProvider(trajectory_path)))
        super().__init__(
            deps,
            agent_name=f"judge-{deps.stage}",
            middleware=mw,
        )
        self._agent: Agent[JudgeDeps, str] = self._build_agent(
            model,
            deps_type=JudgeDeps,
            output_type=str,
            model_settings=thinking_settings(model, THINKING_BUDGET),
            tools=[
                exec_bash_any,
                read_file,
                grep,
                write_file,
                str_replace_file,
                submit_independent_findings,
                reveal_agent_hypothesis,
                submit_verdict,
            ],
        )

        @self._agent.instructions
        def _system(ctx) -> str:
            return _render(f"{ctx.deps.stage}_judge_system")

    async def arun(self, user_prompt: str, run_ctx: dict[str, Any] | None = None) -> tuple[str, dict]:
        """Run with submit reminders. Returns (output, usage)."""
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        message_history: list | None = None
        current_prompt = user_prompt
        reminder_count = 0
        result = None

        while True:
            kwargs: dict[str, Any] = {}
            if message_history is not None:
                kwargs["message_history"] = message_history

            try:
                result = await self._arun(current_prompt, _run_ctx=run_ctx, **kwargs)
            except UnexpectedModelBehavior as exc:
                if self.deps.state.submitted:
                    logger.warning(
                        f"Judge model returned unexpected output after submitting; treating as complete. ({exc})"
                    )
                else:
                    logger.warning(
                        f"Judge model returned unexpected output without submitting; treating as unsubmitted. ({exc})"
                    )
                usage["input_tokens"] += self.current_run_usage.input_tokens or 0
                usage["output_tokens"] += self.current_run_usage.output_tokens or 0
                break
            u = result.usage()
            usage["input_tokens"] += u.input_tokens or 0
            usage["output_tokens"] += u.output_tokens or 0

            if self.deps.state.submitted:
                break

            if reminder_count >= self.MAX_SUBMIT_REMINDERS:
                logger.warning("Max judge submit reminders reached — giving up.")
                break

            reminder_count += 1
            logger.warning(f"Judge stopped without submitting (reminder {reminder_count}/{self.MAX_SUBMIT_REMINDERS}).")
            message_history = list(result.all_messages())
            current_prompt = (
                "You have not submitted your verdict yet. "
                "Please call `submit_verdict` with your final verdict before finishing."
            )

        output = result.output if result is not None else ""
        return output, usage
