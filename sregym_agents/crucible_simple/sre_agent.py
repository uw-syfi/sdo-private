"""Simplified DeepAgents SRE agent factory with optional KB subagents."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from deepagents import create_deep_agent

from sregym_agents.crucible_simple._prompts import _render
from sregym_agents.crucible_simple.shell_backend import TimeoutShellBackend
from sregym_agents.crucible_simple.tools import (
    SREDeps,
    SRESubmission,
    TrajectoryCallbackHandler,
    init_chat_model_with_thinking,
)

logger = logging.getLogger(__name__)


def _build_subagents(
    model_id: str,
    stage: str,
    deps: SREDeps,
) -> list[dict]:
    """Build optional KB subagents for the SRE agent."""
    subagents: list[dict] = []

    if deps.lt_summary_file is None:
        return subagents

    if stage == "diagnosis":
        subagents.append(
            {
                "name": "search-prior-incidents",
                "description": (
                    "Search past incidents for candidate root causes matching "
                    "observed symptoms. Delegate with key anomalies from your "
                    "investigation. Returns a ranked differential diagnosis."
                ),
                "model": deps.ltm_model_id or model_id,
                "system_prompt": _render(
                    "search_prior_incidents",
                    stage=deps.stage,
                    observed_symptoms="(will be provided when you are delegated a task)",
                    triage_context="",
                    lt_summary_file=str(deps.lt_summary_file),
                    incidents_dir=str(deps.incidents_dir) if deps.incidents_dir else "",
                ),
                "tools": [],
            }
        )

    elif stage == "mitigation":
        subagents.append(
            {
                "name": "search-prior-mitigations",
                "description": (
                    "Search past incidents for mitigation strategies matching "
                    "a confirmed root cause. Delegate with the confirmed "
                    "diagnosis to retrieve proven mitigation approaches."
                ),
                "model": deps.ltm_model_id or model_id,
                "system_prompt": _render(
                    "search_prior_mitigations",
                    root_cause="(will be provided when delegated)",
                    failed_attempts="",
                    lt_summary_file=str(deps.lt_summary_file),
                    incidents_dir=str(deps.incidents_dir) if deps.incidents_dir else "",
                ),
                "tools": [],
            }
        )

    return subagents


async def run_sre_agent(
    model_id: str,
    stage: str,
    deps: SREDeps,
    system_prompt: str,
    user_prompt: str,
    trajectory_path: Path | None = None,
    run_ctx: dict[str, Any] | None = None,
) -> tuple[str, dict]:
    """Create and run a DeepAgents SRE agent. Returns (answer, usage_dict)."""
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

    subagents = _build_subagents(model_id, stage, deps)

    agent = create_deep_agent(
        model=init_chat_model_with_thinking(model_id, temperature=0),
        tools=[],
        backend=TimeoutShellBackend(virtual_mode=True, inherit_env=True),
        system_prompt=system_prompt,
        subagents=subagents,
        response_format=SRESubmission,
    )

    callbacks = []
    trajectory_handler: TrajectoryCallbackHandler | None = None
    if trajectory_path is not None:
        trajectory_handler = TrajectoryCallbackHandler(
            trajectory_path,
            agent_name=f"sre-{stage}",
            run_ctx=run_ctx,
        )
        callbacks.append(trajectory_handler)

    config: dict[str, Any] = {}
    if callbacks:
        config["callbacks"] = callbacks

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config=config,
    )

    submission = result.get("structured_response")
    if not isinstance(submission, SRESubmission):
        logger.error(
            "SRE agent did not return a valid SRESubmission. "
            "Got structured_response=%r (type=%s). Full result keys: %s",
            submission,
            type(submission).__name__,
            list(result.keys()),
        )
        raise RuntimeError(
            f"SRE agent failed to produce structured output: expected SRESubmission, got {type(submission).__name__}"
        )

    deps.state.submitted = True
    deps.state.answer = submission.answer
    deps.state.answer_justification = submission.justification
    deps.state.answer_causal_chain = submission.causal_chain
    deps.state.answer_reflection = submission.reflection

    iteration = deps.iteration
    if stage == "diagnosis":
        entry = f"\n### Iteration {iteration} — Agent Hypothesis\n[Submitted — pending judge review]\n"
    else:
        entry = (
            f"\n### Iteration {iteration} — Agent Strategy\n"
            f"**Mitigation**: {submission.answer}\n"
            f"**Justification**: {submission.justification}\n"
        )
    try:
        deps.shared_file.append(entry)
    except Exception as e:
        logger.warning(f"Error writing to shared file: {e}")

    if trajectory_handler is not None:
        trajectory_handler.flush()
        handler_usage = trajectory_handler.usage
        usage["input_tokens"] += handler_usage.get("input_tokens", 0)
        usage["output_tokens"] += handler_usage.get("output_tokens", 0)

    answer = deps.state.answer or ""
    if answer:
        logger.info(f"SRE agent answer: {answer}")

    return answer, usage
