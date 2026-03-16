"""CrucibleSREAgent: pydantic-ai agent for SRE diagnosis/mitigation with judge loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessagesTypeAdapter

from libs.agent_mw import TrajectoryMiddleware, TurnLoggingMiddleware
from libs.pydantic_agent._base import BaseAgent
from sregym_agents.crucible._prompts import _render
from sregym_agents.crucible.middleware import LoopDetectionMiddleware, TimeoutMiddleware
from sregym_agents.crucible.tools import (
    SREDeps,
    exec_bash,
    mark_hypothesis_complete,
    mark_mitigation_complete,
    read_file,
    str_replace_file,
    write_file,
)

logger = logging.getLogger(__name__)

THINKING_BUDGET = 4096


def _thinking_settings_for(model: str) -> dict:
    """Return model_settings dict with thinking budget for supported model families."""
    if "claude" in model or "anthropic" in model:
        return {"anthropic_thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET}}
    if "gemini" in model:
        return {"gemini_thinking_config": {"thinking_budget": THINKING_BUDGET}}
    return {}


_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gemini": 1_000_000,
}


def _context_window_for(model: str) -> int:
    for prefix, window in _CONTEXT_WINDOWS.items():
        if prefix in model:
            return window
    return 128_000


def _compact_messages(model: str, messages: list) -> tuple[str, dict]:
    """Summarize message history for context compaction. Returns (summary, usage)."""
    import json

    to_summarize = messages[1:] if len(messages) > 1 else messages
    try:
        raw = json.loads(ModelMessagesTypeAdapter.dump_json(to_summarize))
        parts = []
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
    compact_result = compactor.run_sync(summary_prompt)
    u = compact_result.usage()
    usage = {
        "input_tokens": u.request_tokens or 0,
        "output_tokens": u.response_tokens or 0,
        "cached_input_tokens": 0,
    }
    logger.info(f"Context compacted: {len(history_text)} chars → {len(compact_result.output)} chars")
    return compact_result.output, usage


class CrucibleSREAgent(BaseAgent[SREDeps]):
    MAX_SUBMIT_REMINDERS = 3
    CONTEXT_COMPACT_THRESHOLD = 0.80

    def __init__(self, model: str, deps: SREDeps, trajectory_path: Path | None = None) -> None:
        mw = [TurnLoggingMiddleware(), LoopDetectionMiddleware(), TimeoutMiddleware()]
        if trajectory_path is not None:
            mw.insert(0, TrajectoryMiddleware(trajectory_path))
        super().__init__(
            deps,
            agent_name=f"sre-{deps.stage}",
            middleware=mw,
        )
        self._model = model
        self._agent: Agent[SREDeps, str] = Agent(
            model,
            deps_type=SREDeps,
            output_type=str,
            model_settings=_thinking_settings_for(model),
            tools=[
                exec_bash,
                read_file,
                write_file,
                str_replace_file,
                mark_hypothesis_complete,
                mark_mitigation_complete,
            ],
        )

        @self._agent.instructions
        def _system(ctx) -> str:
            return _render(f"{ctx.deps.stage}_agent_system")

    def run(self, user_prompt: str, run_ctx: dict[str, Any] | None = None) -> tuple[str, dict]:
        """Run with submit reminders and context compaction. Returns (output, usage)."""
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        message_history: list | None = None
        current_prompt = user_prompt
        reminder_count = 0
        context_window = _context_window_for(self._model)
        initial_prompt = user_prompt
        result = None

        while True:
            kwargs: dict[str, Any] = {}
            if message_history is not None:
                kwargs["message_history"] = message_history

            try:
                result = self._run(current_prompt, _run_ctx=run_ctx, **kwargs)
            except UnexpectedModelBehavior as exc:
                if self.deps.state.submitted:
                    logger.warning(f"Model returned unexpected output after submitting; treating as complete. ({exc})")
                    usage["input_tokens"] += self.current_run_usage.request_tokens or 0
                    usage["output_tokens"] += self.current_run_usage.response_tokens or 0
                    break
                raise
            u = result.usage()
            input_tokens = u.request_tokens or 0
            output_tokens = u.response_tokens or 0
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens

            if self.deps.state.submitted:
                break

            if reminder_count >= self.MAX_SUBMIT_REMINDERS:
                logger.warning("Max submit reminders reached — giving up.")
                break

            if input_tokens > self.CONTEXT_COMPACT_THRESHOLD * context_window:
                logger.warning(
                    f"Context approaching limit ({input_tokens} > "
                    f"{self.CONTEXT_COMPACT_THRESHOLD * context_window:.0f}). Compacting..."
                )
                summary, compact_usage = _compact_messages(self._model, result.all_messages())
                for k, v in compact_usage.items():
                    usage[k] = usage.get(k, 0) + v
                message_history = None
                current_prompt = initial_prompt + "\n\nHere's a summary of the previous conversation:\n\n" + summary
                logger.warning("Context compacted. Restarting with summary.")
                continue

            reminder_count += 1
            stage = self.deps.stage
            tool_name = "mark_hypothesis_complete" if stage == "diagnosis" else "mark_mitigation_complete"
            logger.warning(f"Agent stopped without submitting (reminder {reminder_count}/{self.MAX_SUBMIT_REMINDERS}).")
            message_history = list(result.all_messages())
            current_prompt = (
                f"You have not submitted your answer yet. "
                f"Please call `{tool_name}` with your final answer before finishing."
            )

        output = result.output if result is not None else ""
        if output:
            logger.info(f"Agent summary: {output}")
        return output, usage
