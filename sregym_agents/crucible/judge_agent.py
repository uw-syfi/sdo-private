"""CrucibleJudgeAgent: pydantic-ai judge agent for the dual-agent judge loop."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent

from libs.pydantic_agent._base import BaseAgent
from sregym_agents.crucible._prompts import _render
from sregym_agents.crucible.middleware import LoopDetectionMiddleware, TimeoutMiddleware
from sregym_agents.crucible.tools import (
    JudgeDeps,
    exec_bash_readonly,
    read_file,
    submit_verdict,
)

logger = logging.getLogger(__name__)


class CrucibleJudgeAgent(BaseAgent[JudgeDeps]):
    MAX_SUBMIT_REMINDERS = 3

    def __init__(self, model: str, deps: JudgeDeps) -> None:
        super().__init__(
            deps,
            middleware=[
                LoopDetectionMiddleware(),
                TimeoutMiddleware(),
            ],
        )
        self._agent: Agent[JudgeDeps, str] = Agent(
            model,
            deps_type=JudgeDeps,
            output_type=str,
            tools=[exec_bash_readonly, read_file, submit_verdict],
        )

        @self._agent.instructions
        def _system(ctx) -> str:
            return _render(f"{ctx.deps.stage}_judge_system")

    def run(self, user_prompt: str) -> tuple[str, dict]:
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

            result = self._run(current_prompt, **kwargs)
            u = result.usage()
            usage["input_tokens"] += u.request_tokens or 0
            usage["output_tokens"] += u.response_tokens or 0

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
