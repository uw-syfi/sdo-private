"""Knowledge-base retrieval tools for the Crucible SRE agent pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

from libs.agent_mw import arun_with_retry
from sregym_agents.crucible._prompts import (
    PromptRenderer,  # noqa: TC001 — needed at runtime for pydantic-ai tool introspection
)
from sregym_agents.crucible.tools._bash_tools import exec_bash_any, grep, read_file, str_replace_file, write_file
from sregym_agents.crucible.tools._deps import (
    SREDeps,  # noqa: TC001 — needed at runtime for pydantic-ai tool introspection
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

THINKING_BUDGET = 4096
MAX_OUTPUT_TOKENS = 16_384
VERIFICATION_THINKING_BUDGET = 2048
COVERAGE_THINKING_BUDGET = 2048


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TriageAnomaly(BaseModel):
    category: str = Field(
        description="Anomaly category — use a short descriptive label "
        "(e.g., 'Non-Running Pods', 'Port Mismatch', 'Services Without Endpoints', "
        "'ConfigMap Anomalies', 'Recent Events'). "
        "Use standard categories when they fit; create new ones for novel anomaly types."
    )
    resource_kind: str = Field(description="Kubernetes resource kind (e.g., Pod, Service, ConfigMap)")
    resource_name: str = Field(description="Name of the resource")
    namespace: str = Field(description="Namespace of the resource")
    observation: str = Field(description="Factual description of the anomaly — no interpretation")


class TriageReport(BaseModel):
    anomalies: list[TriageAnomaly] = Field(
        default_factory=list, description="All observed anomalies, each tagged with a category"
    )
    raw_cluster_snapshot: str = Field(
        default="", description="Condensed kubectl output for downstream agents to reference"
    )


class CandidateRootCause(BaseModel):
    root_cause_class: str = Field(
        description=(
            "Abstract class of root cause (e.g., 'missing Kubernetes Service',"
            " 'targetPort mismatch', 'DNS policy override')"
        )
    )
    root_cause: str = Field(
        description=(
            "Natural-language description of the failure pattern to look for."
            " Do NOT name specific services, ports, or field values."
        )
    )
    distinguishing_check: str = Field(
        description=(
            "Natural-language investigation strategy describing what to check"
            " across ALL resources of the relevant class. Not a specific kubectl"
            " command targeting a single resource."
        )
    )
    mitigation_hint: str = Field(description="Generic mitigation approach for this root cause class")


class DifferentialDiagnosis(BaseModel):
    candidate_root_causes: list[CandidateRootCause] = Field(
        description="Top 1-3 candidate root causes for the observed symptoms, ordered by relevance"
    )
    novel_cause_signals: str = Field(
        description="What to look for if none of the candidates match — signals indicating a novel root cause"
    )
    caveats: str = Field(description="What doesn't match; what to verify before assuming patterns apply")


class CandidateVerification(BaseModel):
    """Result of verifying whether a candidate root cause applies to the current incident."""

    candidate_index: int = Field(description="Index of the candidate in the differential diagnosis list")
    root_cause_class: str = Field(description="The candidate's root_cause_class (echoed for context)")
    root_cause: str = Field(description="The candidate's root_cause (echoed for context)")
    applies: bool = Field(description="True if evidence confirms this candidate applies; False if ruled out")
    causal_chain: str = Field(
        default="",
        description=(
            "If applies: full chain from misconfigured field → mechanism → observed symptom. Empty if ruled out."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence items (command outputs, log lines, field values) supporting the conclusion",
    )
    reasoning: str = Field(description="Explanation of why this candidate was confirmed or ruled out")


class VerifiedDifferentialDiagnosis(BaseModel):
    """Differential diagnosis with each candidate verified by a subagent."""

    verified_candidates: list[CandidateVerification] = Field(
        description="Verification results for each candidate, ordered by original rank"
    )
    confirmed_candidates: list[CandidateVerification] = Field(
        default_factory=list,
        description="Subset of verified_candidates where applies=True, for convenience",
    )
    novel_cause_signals: str = Field(description="What to look for if none of the candidates match")
    caveats: str = Field(default="", description="What doesn't match; what to verify before assuming patterns apply")


class HypothesisCoverageVerdict(BaseModel):
    """Result of checking whether a hypothesis explains all triage anomalies."""

    verdict: str = Field(description="'accept' if the hypothesis explains all anomalies, 'reject' otherwise")
    explained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis explains (including pre-existing noise)",
    )
    unexplained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis does NOT explain",
    )
    reasoning: str = Field(description="Explanation of coverage assessment")


class MitigationStrategy(BaseModel):
    """A mitigation strategy retrieved from the knowledge base."""

    root_cause_class: str = Field(description="The root cause class this mitigation addresses")
    mitigation_approach: str = Field(description="Generic mitigation approach from the knowledge base")
    detailed_steps: str = Field(
        default="",
        description=(
            "Detailed mitigation steps extracted from referenced incident files, "
            "if available. Includes specific commands or procedures used in past incidents."
        ),
    )
    incident_refs: list[str] = Field(
        default_factory=list,
        description="Incident file references where this mitigation was applied",
    )
    caveats: str = Field(
        default="",
        description="Warnings or conditions under which this mitigation may not apply",
    )


class MitigationSearchResult(BaseModel):
    """Result of searching the knowledge base for mitigation strategies."""

    strategies: list[MitigationStrategy] = Field(
        description=(
            "Mitigation strategies matching the confirmed root cause, "
            "ordered by relevance. Empty if no matching root cause class found in KB."
        ),
    )
    novel_cause: bool = Field(
        default=False,
        description="True if the root cause does not match any known class in the KB",
    )
    general_guidance: str = Field(
        default="",
        description=("General mitigation guidance when no exact match is found, or additional context from the KB"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_triage_report(report: TriageReport) -> str:
    """Convert a TriageReport to readable markdown."""
    lines = ["### Triage Report"]
    if not report.anomalies:
        lines.append("\nNo anomalies detected.")
        return "\n".join(lines) + "\n"
    # Group by category, preserving first-seen order.
    grouped: dict[str, list[TriageAnomaly]] = {}
    for a in report.anomalies:
        grouped.setdefault(a.category, []).append(a)
    for category, anomalies in grouped.items():
        lines.append(f"\n**{category}**")
        lines.extend(f"- `{a.resource_kind}/{a.resource_name}` ({a.namespace}): {a.observation}" for a in anomalies)
    return "\n".join(lines) + "\n"


async def _ltm_stream_handler(ctx: Any, events: Any) -> None:
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        RetryPromptPart,
        ThinkingPart,
        ToolReturnPart,
    )

    from libs.agent_mw import fmt_tool_args, tool_call_failed

    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            logger.info("[ltm-search] → %s(%s)", event.part.tool_name, fmt_tool_args(event.part.args))
        elif isinstance(event, FunctionToolResultEvent):
            result = event.result
            if isinstance(result, RetryPromptPart):
                logger.warning(
                    "[ltm-search] ✗ %s() failed: %s",
                    result.tool_name or "unknown",
                    result.model_response(),
                )
            elif isinstance(result, ToolReturnPart) and tool_call_failed(result.content):
                logger.warning(
                    "[ltm-search] ✗ %s() exited with code %s: %s",
                    result.tool_name,
                    result.content.get("exit_code", "?"),
                    result.content.get("stderr", ""),
                )
        elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("[ltm-search] <thinking> %s", event.part.content)


def _make_verify_stream_handler(idx: int):
    """Create a stream handler that logs with [ltm-verify-{idx}] prefix."""
    prefix = f"[ltm-verify-{idx}]"

    async def _handler(ctx: Any, events: Any) -> None:
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            FunctionToolResultEvent,
            PartEndEvent,
            RetryPromptPart,
            ThinkingPart,
            ToolReturnPart,
        )

        from libs.agent_mw import fmt_tool_args, tool_call_failed

        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                logger.info("%s → %s(%s)", prefix, event.part.tool_name, fmt_tool_args(event.part.args))
            elif isinstance(event, FunctionToolResultEvent):
                result = event.result
                if isinstance(result, RetryPromptPart):
                    logger.warning(
                        "%s ✗ %s() failed: %s",
                        prefix,
                        result.tool_name or "unknown",
                        result.model_response(),
                    )
                elif isinstance(result, ToolReturnPart) and tool_call_failed(result.content):
                    logger.warning(
                        "%s ✗ %s() exited with code %s: %s",
                        prefix,
                        result.tool_name,
                        result.content.get("exit_code", "?"),
                        result.content.get("stderr", ""),
                    )
            elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
                logger.info("%s <thinking> %s", prefix, event.part.content)

    return _handler


async def _triage_stream_handler(ctx: Any, events: Any) -> None:
    """Stream handler that logs triage subagent events with [triage] prefix."""
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        RetryPromptPart,
        ThinkingPart,
        ToolReturnPart,
    )

    from libs.agent_mw import fmt_tool_args, tool_call_failed

    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            logger.info("[triage] → %s(%s)", event.part.tool_name, fmt_tool_args(event.part.args))
        elif isinstance(event, FunctionToolResultEvent):
            result = event.result
            if isinstance(result, RetryPromptPart):
                logger.warning(
                    "[triage] ✗ %s() failed: %s",
                    result.tool_name or "unknown",
                    result.model_response(),
                )
            elif isinstance(result, ToolReturnPart) and tool_call_failed(result.content):
                logger.warning(
                    "[triage] ✗ %s() exited with code %s: %s",
                    result.tool_name,
                    result.content.get("exit_code", "?"),
                    result.content.get("stderr", ""),
                )
        elif isinstance(event, PartEndEvent) and isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("[triage] <thinking> %s", event.part.content)


def _write_trajectory_record(
    trajectory_path: Path,
    agent_name: str,
    result: Any,
    run_ctx: dict[str, Any] | None = None,
) -> None:
    """Write a single trajectory record for an inline agent run (same format as TrajectoryMiddleware)."""
    from datetime import datetime

    from pydantic_ai.messages import ModelMessagesTypeAdapter

    u = result.usage()
    record = {
        "agent_name": agent_name,
        "timestamp": datetime.now().isoformat(),
        "run_ctx": run_ctx,
        "messages": ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json"),
        "usage": {
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
        },
    }
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trajectory_path, "a") as f:
        f.write(json.dumps(record) + "\n")


async def _run_verification_phase(
    candidates: list[CandidateRootCause],
    diagnosis: DifferentialDiagnosis,
    observed_symptoms: str,
    namespace: str,
    stage: str,
    model_id: Model | str,
    renderer: PromptRenderer,
    trajectory_path: Path | None = None,
    triage_report: TriageReport | None = None,
) -> VerifiedDifferentialDiagnosis:
    """Spawn one verification subagent per candidate in parallel and return aggregated results."""
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings

    triage_context = ""
    if triage_report is not None:
        triage_context = format_triage_report(triage_report)

    async def _verify_one(idx: int, candidate: CandidateRootCause) -> CandidateVerification:
        prompt = renderer.render(
            "ltm_verify_candidate",
            namespace=namespace,
            stage=stage,
            observed_symptoms=observed_symptoms,
            triage_context=triage_context,
            candidate_index=idx,
            root_cause_class=candidate.root_cause_class,
            root_cause=candidate.root_cause,
            distinguishing_check=candidate.distinguishing_check,
            mitigation_hint=candidate.mitigation_hint,
        )
        logger.info("[ltm-verify-%d] PROMPT:\n%s", idx, prompt)

        verify_agent: Agent[None, CandidateVerification] = Agent(
            model_id,
            output_type=CandidateVerification,
            tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
            model_settings=thinking_settings(model_id, VERIFICATION_THINKING_BUDGET),
        )
        try:
            result = await arun_with_retry(
                verify_agent,
                prompt,
                event_stream_handler=_make_verify_stream_handler(idx),
            )
            output = result.output
            # Ensure echoed fields match the candidate
            output.candidate_index = idx
            output.root_cause_class = candidate.root_cause_class
            output.root_cause = candidate.root_cause

            if trajectory_path is not None:
                _write_trajectory_record(
                    trajectory_path,
                    f"ltm-verify-{idx}",
                    result,
                    run_ctx={"stage": stage, "role": "ltm-verify", "candidate_index": idx},
                )

            logger.info(
                "[ltm-verify-%d] done: applies=%s, reasoning=%s",
                idx,
                output.applies,
                output.reasoning,
            )
            return output
        except Exception as e:
            logger.warning("[ltm-verify-%d] failed: %s", idx, e)
            return CandidateVerification(
                candidate_index=idx,
                root_cause_class=candidate.root_cause_class,
                root_cause=candidate.root_cause,
                applies=False,
                reasoning=f"Verification failed with error: {e}",
            )

    verifications = await asyncio.gather(
        *[_verify_one(i, c) for i, c in enumerate(candidates)],
    )

    confirmed = [v for v in verifications if v.applies]

    return VerifiedDifferentialDiagnosis(
        verified_candidates=list(verifications),
        confirmed_candidates=confirmed,
        novel_cause_signals=diagnosis.novel_cause_signals,
        caveats=diagnosis.caveats,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def triage_cluster(
    ctx: RunContext[SREDeps],
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Call this FIRST, before search_prior_incidents. Returns a structured triage
    report listing all anomalous resources (non-running pods, services without
    endpoints, misconfigurations, etc.). Pass the output to search_prior_incidents
    as part of your observed_symptoms.
    """
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings

    model_id = ctx.deps.ltm_model_id
    if not model_id:
        return "Error: triage_cluster requires a model ID (ltm_model_id not set)."

    prompt = ctx.deps.renderer.render(
        "triage_cluster",
        namespace=ctx.deps.namespace,
        triage_guidance=ctx.deps.triage_guidance,
    )
    logger.info("[triage-cluster] PROMPT:\n%s", prompt)

    triage_agent: Agent[None, TriageReport] = Agent(
        model_id,
        output_type=TriageReport,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
    )

    @triage_agent.output_validator
    def _require_tool_calls(ctx: RunContext[None], report: TriageReport) -> TriageReport:
        from pydantic_ai.messages import ToolCallPart

        has_calls = any(isinstance(part, ToolCallPart) for msg in ctx.messages for part in msg.parts)
        if not has_calls:
            raise ModelRetry(
                "You MUST use exec_bash_any to run kubectl commands before "
                "producing the triage report. You have not called any tools yet. "
                "Run the recommended kubectl commands now."
            )
        return report

    try:
        result = await arun_with_retry(
            triage_agent,
            prompt,
            event_stream_handler=_triage_stream_handler,
        )
        report = result.output
        ctx.deps.triage_report = report

        if ctx.deps.trajectory_path is not None:
            _write_trajectory_record(
                ctx.deps.trajectory_path,
                "triage",
                result,
                run_ctx={"stage": ctx.deps.stage, "role": "triage"},
            )

        formatted = format_triage_report(report)
        logger.info("[triage] done: %s", formatted)
        if ctx.deps.stage_outputs_file:
            with open(ctx.deps.stage_outputs_file, "a") as f:
                f.write(f"\n## Triage Report\n{formatted}\n")
        return formatted
    except Exception as e:
        logger.warning("[triage] failed: %s", e)
        return f"Triage failed with error: {e}. Proceed with manual investigation."


async def check_hypothesis_coverage(
    ctx: RunContext[SREDeps],
    hypothesis: str,
) -> str:
    """Check whether your hypothesis explains ALL anomalies in the triage report.

    Call this BEFORE submitting your diagnosis. Pass your proposed root cause
    (including the specific resource, misconfigured field, and causal chain).
    Returns accept/reject with reasoning about which triage anomalies are
    unexplained. If rejected, revise your hypothesis to account for the
    unexplained anomalies before submitting.
    """
    from pydantic_ai import Agent

    from libs.pydantic_agent import thinking_settings

    triage_report = ctx.deps.triage_report
    if triage_report is None:
        return "Error: no triage report available. Call triage_cluster first."

    model_id = ctx.deps.ltm_model_id
    if not model_id:
        return "Error: check_hypothesis_coverage requires a model ID (ltm_model_id not set)."

    triage_context = format_triage_report(triage_report)
    prompt = ctx.deps.renderer.render(
        "check_hypothesis_coverage",
        triage_context=triage_context,
        hypothesis=hypothesis,
        arbitration_guidance=ctx.deps.arbitration_guidance,
    )
    logger.info("[hypothesis-coverage] PROMPT:\n%s", prompt)

    coverage_agent: Agent[None, HypothesisCoverageVerdict] = Agent(
        model_id,
        output_type=HypothesisCoverageVerdict,
        model_settings=thinking_settings(model_id, COVERAGE_THINKING_BUDGET),
    )

    try:
        result = await arun_with_retry(coverage_agent, prompt)
        output = result.output

        if ctx.deps.trajectory_path is not None:
            _write_trajectory_record(
                ctx.deps.trajectory_path,
                "hypothesis-coverage",
                result,
                run_ctx={"stage": ctx.deps.stage, "role": "hypothesis-coverage"},
            )

        output_json = output.model_dump_json(indent=2)
        logger.info(
            "[hypothesis-coverage] done: verdict=%s, unexplained=%s, reasoning=%s",
            output.verdict,
            output.unexplained_anomalies,
            output.reasoning,
        )
        if ctx.deps.stage_outputs_file:
            with open(ctx.deps.stage_outputs_file, "a") as f:
                f.write(f"\n## Hypothesis Coverage Check\n{output_json}\n")
        return output_json
    except Exception as e:
        logger.warning("[hypothesis-coverage] failed: %s", e)
        return f"Coverage check failed with error: {e}. Submit your best hypothesis."


async def search_prior_incidents(
    ctx: RunContext[SREDeps],
    observed_symptoms: str,
) -> str:
    """Search past incidents and return verified candidate root causes.

    Call EARLY with observed symptoms from quick triage — a full hypothesis is
    NOT required. Factual symptom descriptions work well (e.g., "pod X
    CrashLoopBackOff, OOMKilled exit code 137, service Y no endpoints"). Can
    also be called later with a refined hypothesis. Returns candidates that have
    been verified by parallel subagents — each candidate includes whether it
    applies, a causal chain if confirmed, and reasoning. Budget: 1 call per stage.

    Args:
        observed_symptoms: Factual description of current observations or hypothesis.
    """
    empty_result = (
        '{"verified_candidates": [], "confirmed_candidates": [],'
        ' "novel_cause_signals": "", "caveats": "No incident history available."}'
    )
    if not ctx.deps.lt_summary_file:
        logger.info("[ltm-search] skipped (no summary file): %s", empty_result)
        return empty_result

    if not observed_symptoms.strip():
        result = "Error: observed_symptoms must not be empty."
        logger.info("[ltm-search] skipped (empty symptoms): %s", result)
        return result

    if ctx.deps.ltm_call_count >= ctx.deps.ltm_call_budget:
        result = (
            '{"verified_candidates": [], "confirmed_candidates": [],'
            ' "novel_cause_signals": "",'
            ' "caveats": "Search budget exhausted. Proceed with independent investigation."}'
        )
        logger.info("[ltm-search] skipped (budget exhausted): %s", result)
        return result
    ctx.deps.ltm_call_count += 1

    ltm_model_id = ctx.deps.ltm_model_id
    if not ltm_model_id:
        return "Error: search_prior_incidents requires a model ID (ltm_model_id not set)."

    from pydantic_ai import Agent

    triage_context = ""
    if ctx.deps.triage_report is not None:
        triage_context = format_triage_report(ctx.deps.triage_report)

    prompt = ctx.deps.renderer.render(
        "search_prior_incidents",
        stage=ctx.deps.stage,
        observed_symptoms=observed_symptoms,
        triage_context=triage_context,
        lt_summary_file=str(ctx.deps.lt_summary_file),
        incidents_dir=str(ctx.deps.incidents_dir) if ctx.deps.incidents_dir else "",
    )
    logger.info("[ltm-search] PROMPT:\n%s", prompt)

    from libs.pydantic_agent import thinking_settings

    retrieval_agent: Agent[None, DifferentialDiagnosis] = Agent(
        ltm_model_id,
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(ltm_model_id, THINKING_BUDGET),
    )
    retrieval_result = await arun_with_retry(retrieval_agent, prompt, event_stream_handler=_ltm_stream_handler)
    diagnosis = retrieval_result.output
    logger.info("[ltm-search] retrieval output: %s", diagnosis.model_dump_json(indent=2))

    if not diagnosis.candidate_root_causes:
        verified = VerifiedDifferentialDiagnosis(
            verified_candidates=[],
            confirmed_candidates=[],
            novel_cause_signals=diagnosis.novel_cause_signals,
            caveats=diagnosis.caveats,
        )
        output_json = verified.model_dump_json(indent=2)
        logger.info("[ltm-search] no candidates to verify: %s", output_json)
        return output_json

    verified = await _run_verification_phase(
        candidates=diagnosis.candidate_root_causes,
        diagnosis=diagnosis,
        observed_symptoms=observed_symptoms,
        namespace=ctx.deps.namespace,
        stage=ctx.deps.stage,
        model_id=ltm_model_id,
        renderer=ctx.deps.renderer,
        trajectory_path=ctx.deps.trajectory_path,
        triage_report=ctx.deps.triage_report,
    )
    output_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-search] verified output: %s", output_json)
    if ctx.deps.stage_outputs_file:
        with open(ctx.deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Retrieval Results\n{output_json}\n")
    return output_json


async def search_prior_mitigations(
    ctx: RunContext[SREDeps],
    root_cause: str,
    failed_attempts: str = "",
) -> str:
    """Search past incidents for mitigation strategies matching a confirmed root cause.

    Call with the confirmed diagnosis to retrieve proven mitigation approaches
    from past incidents. Optionally include failed_attempts to exclude strategies
    that were already tried unsuccessfully. Budget: 1 call per stage (shared with
    search_prior_incidents).

    Args:
        root_cause: The confirmed root cause diagnosis to find mitigations for.
        failed_attempts: Description of mitigation attempts that already failed (optional).
    """
    empty_result = '{"strategies": [], "novel_cause": false, "general_guidance": "No incident history available."}'
    if not ctx.deps.lt_summary_file:
        logger.info("[ltm-mitigation] skipped (no summary file): %s", empty_result)
        return empty_result

    if not root_cause.strip():
        result = "Error: root_cause must not be empty."
        logger.info("[ltm-mitigation] skipped (empty root_cause): %s", result)
        return result

    if ctx.deps.ltm_call_count >= ctx.deps.ltm_call_budget:
        result = (
            '{"strategies": [], "novel_cause": false,'
            ' "general_guidance": "Search budget exhausted. Proceed with independent mitigation."}'
        )
        logger.info("[ltm-mitigation] skipped (budget exhausted): %s", result)
        return result
    ctx.deps.ltm_call_count += 1

    ltm_model_id = ctx.deps.ltm_model_id
    if not ltm_model_id:
        return "Error: search_prior_mitigations requires a model ID (ltm_model_id not set)."

    from pydantic_ai import Agent

    prompt = ctx.deps.renderer.render(
        "search_prior_mitigations",
        root_cause=root_cause,
        failed_attempts=failed_attempts,
        lt_summary_file=str(ctx.deps.lt_summary_file),
        incidents_dir=str(ctx.deps.incidents_dir) if ctx.deps.incidents_dir else "",
    )
    logger.info("[ltm-mitigation] PROMPT:\n%s", prompt)

    from libs.pydantic_agent import thinking_settings

    retrieval_agent: Agent[None, MitigationSearchResult] = Agent(
        ltm_model_id,
        output_type=MitigationSearchResult,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(ltm_model_id, THINKING_BUDGET),
    )
    retrieval_result = await arun_with_retry(retrieval_agent, prompt, event_stream_handler=_ltm_stream_handler)
    output = retrieval_result.output
    output_json = output.model_dump_json(indent=2)
    logger.info("[ltm-mitigation] output: %s", output_json)
    return output_json
