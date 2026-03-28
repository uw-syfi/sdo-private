"""DeepAgents SRE agent factory with native subagent definitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.chat_models import init_chat_model

from sregym_agents.crucible_deepagents._prompts import _render
from sregym_agents.crucible_deepagents.tools import (
    SREDeps,
    SRESubmission,
    TrajectoryCallbackHandler,
)

logger = logging.getLogger(__name__)


def _build_subagents(
    model_id: str,
    stage: str,
    deps: SREDeps,
) -> list[dict]:
    """Build the list of DeepAgents native subagent dicts for the SRE agent."""
    subagents: list[dict] = []

    if stage == "diagnosis":
        # Triage subagent — systematically audits the K8s namespace
        subagents.append(
            {
                "name": "triage-cluster",
                "description": (
                    "Systematically audit the Kubernetes namespace for unhealthy "
                    "components. Delegate to this agent FIRST, before investigating "
                    "specific hypotheses. It will run kubectl commands and return a "
                    "structured triage report listing all anomalous resources."
                ),
                "model": model_id,
                "system_prompt": _render("triage_cluster", namespace=deps.namespace),
                "tools": [],  # uses built-in execute, read_file, grep
            }
        )

        # Search prior incidents subagent — KB retrieval + verification
        if deps.lt_summary_file is not None:
            subagents.append(
                {
                    "name": "search-prior-incidents",
                    "description": (
                        "Search past incidents for candidate root causes matching "
                        "observed symptoms. Delegate to this agent EARLY with "
                        "symptoms from triage. It reads the knowledge base summary "
                        "and incident files, produces a differential diagnosis, and "
                        "verifies each candidate against the live cluster. "
                        "Budget: 1 call per stage."
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

        # Hypothesis coverage check subagent
        if deps.lt_summary_file is None:
            subagents.append(
                {
                    "name": "check-hypothesis-coverage",
                    "description": (
                        "Check whether your hypothesis explains ALL anomalies "
                        "in the triage report. Delegate to this agent BEFORE "
                        "submitting your diagnosis. It returns accept/reject with "
                        "reasoning about which triage anomalies are unexplained."
                    ),
                    "model": deps.ltm_model_id or model_id,
                    "system_prompt": _render(
                        "check_hypothesis_coverage",
                        triage_context="(triage context will be provided when delegated)",
                        hypothesis="(hypothesis will be provided when delegated)",
                    ),
                    "tools": [],
                }
            )

    elif stage == "mitigation":
        # Search prior mitigations subagent
        if deps.lt_summary_file is not None:
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

    # Build native subagents
    subagents = _build_subagents(model_id, stage, deps)

    # Create the DeepAgents agent with structured output
    agent = create_deep_agent(
        model=init_chat_model(model_id, temperature=0),
        tools=[],
        backend=LocalShellBackend(virtual_mode=True, inherit_env=True),
        system_prompt=system_prompt,
        subagents=subagents,
        response_format=SRESubmission,
    )

    # Set up trajectory callback
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

    # Run the agent
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config=config,
    )

    # Extract structured output from the response_format state key
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

    # Write to shared file
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

    # Extract usage from trajectory handler
    if trajectory_handler is not None:
        trajectory_handler.flush()
        handler_usage = trajectory_handler.usage
        usage["input_tokens"] += handler_usage.get("input_tokens", 0)
        usage["output_tokens"] += handler_usage.get("output_tokens", 0)

    answer = deps.state.answer or ""
    if answer:
        logger.info(f"SRE agent answer: {answer}")

    return answer, usage
