"""CrucibleSREAgent: pydantic-ai agent for SRE diagnosis/mitigation with judge loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import Model

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
    arun_with_retry,
)
from libs.pydantic_agent import AgentMiddleware, BaseAgent, thinking_settings
from sregym_agents.crucible.tools import (
    MAX_OUTPUT_TOKENS,
    THINKING_BUDGET,
    SREDeps,
    SRESubmission,
    check_hypothesis_coverage,
    exec_bash,
    grep,
    read_file,
    search_prior_incidents,
    search_prior_mitigations,
    str_replace_file,
    triage_cluster,
    write_file,
)

logger = logging.getLogger(__name__)


_CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "gemini": 1_000_000,
}


def _context_window_for(model: str | Model) -> int:
    name = model.model_name if isinstance(model, Model) else model
    for prefix, window in _CONTEXT_WINDOWS.items():
        if prefix in name:
            return window
    return 128_000


async def _compact_messages(model: str | Model, messages: list[Any]) -> tuple[str, dict[str, int]]:
    """Summarize message history for context compaction. Returns (summary, usage)."""
    import json

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
    u = compact_result.usage()
    usage = {
        "input_tokens": u.input_tokens or 0,
        "output_tokens": u.output_tokens or 0,
        "cached_input_tokens": 0,
    }
    logger.info(f"Context compacted: {len(history_text)} chars → {len(compact_result.output)} chars")
    return compact_result.output, usage


class CrucibleSREAgent(BaseAgent[SREDeps]):
    CONTEXT_COMPACT_THRESHOLD = 0.80

    def __init__(
        self,
        model: Model,
        deps: SREDeps,
        trajectory_path: Path | None = None,
        step_limit: int | None = 500,
        system_prompt_override: str | None = None,
    ) -> None:
        mw: list[AgentMiddleware] = [
            TurnLoggingMiddleware(),
            RetryMiddleware(),
            ThinkingRepetitionMiddleware(),
            LoopDetectionMiddleware(),
            StallDetectionMiddleware(),
            TimeoutMiddleware(),
            SoftLimitExtension(step_limit),
        ]
        if trajectory_path is not None:
            mw.insert(0, TrajectoryMiddleware(FixedPathProvider(trajectory_path)))
        super().__init__(
            deps,
            agent_name=f"sre-{deps.stage}",
            middleware=mw,
        )
        self._model = model
        self._system_prompt_override = system_prompt_override
        self.last_run_messages: list[Any] = []
        self._agent: Agent[SREDeps, SRESubmission] = self._build_agent(
            model,
            deps_type=SREDeps,
            output_type=SRESubmission,
            model_settings={**thinking_settings(model, THINKING_BUDGET), "max_tokens": MAX_OUTPUT_TOKENS},
            tools=[
                exec_bash,
                read_file,
                grep,
                write_file,
                str_replace_file,
                *([] if deps.stage == "mitigation" else [triage_cluster]),
                search_prior_incidents if deps.stage == "diagnosis" else search_prior_mitigations,
                *([] if deps.stage == "mitigation" else [check_hypothesis_coverage]),
            ],
        )

        @self._agent.instructions
        def _system(ctx: RunContext[SREDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
            if self._system_prompt_override:
                return self._system_prompt_override
            return ctx.deps.renderer.render(f"{ctx.deps.stage}_agent_system")

    async def arun(self, user_prompt: str, run_ctx: dict[str, Any] | None = None) -> tuple[str, dict[str, int]]:
        """Run with context compaction. Returns (output, usage)."""
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        context_window = _context_window_for(self._model)
        initial_prompt = user_prompt
        current_prompt = user_prompt
        result = None

        while True:
            try:
                result = await self._arun(current_prompt, _run_ctx=run_ctx)
            except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
                logger.warning(f"Model returned unexpected output; treating as unsubmitted. ({exc})")
                usage["input_tokens"] += self.current_run_usage.input_tokens or 0
                usage["output_tokens"] += self.current_run_usage.output_tokens or 0
                break

            u = result.usage()
            input_tokens = u.input_tokens or 0
            output_tokens = u.output_tokens or 0
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens

            output = result.output
            self.last_run_messages = list(result.all_messages())
            self.deps.state.submitted = True
            self.deps.state.answer = output.answer
            self.deps.state.answer_justification = output.justification
            self.deps.state.answer_causal_chain = output.causal_chain
            self.deps.state.answer_reflection = output.reflection

            iteration = self.deps.iteration
            if self.deps.stage == "diagnosis":
                entry = f"\n### Iteration {iteration} — Agent Hypothesis\n[Submitted — pending judge review]\n"
            else:
                entry = (
                    f"\n### Iteration {iteration} — Agent Strategy\n"
                    f"**Mitigation**: {output.answer}\n"
                    f"**Justification**: {output.justification}\n"
                )
            try:
                self.deps.shared_file.append(entry)
            except Exception as e:
                logger.warning(f"Error writing to shared file: {e}")

            last_request_tokens = self.context_window_token_usage
            if last_request_tokens > self.CONTEXT_COMPACT_THRESHOLD * context_window:
                logger.warning(
                    f"Context approaching limit ({last_request_tokens} > "
                    f"{self.CONTEXT_COMPACT_THRESHOLD * context_window:.0f}). Compacting..."
                )
                summary, compact_usage = await _compact_messages(self._model, result.all_messages())
                for k, v in compact_usage.items():
                    usage[k] = usage.get(k, 0) + v
                current_prompt = initial_prompt + "\n\nHere's a summary of the previous conversation:\n\n" + summary
                logger.warning("Context compacted. Restarting with summary.")
                continue

            break

        answer = self.deps.state.answer or ""
        if answer:
            logger.info(f"Agent answer: {answer}")
        return answer, usage
