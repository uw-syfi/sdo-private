"""Knowledge-base retrieval tools for the Crucible SRE agent pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import ModelRetry, RunContext

from libs.agent_mw import FixedPathProvider, RetryMiddleware, TrajectoryMiddleware, TurnLoggingMiddleware
from libs.pydantic_agent import InlineAgent, thinking_settings
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

import yaml

logger = logging.getLogger(__name__)

THINKING_BUDGET = 4096
MAX_OUTPUT_TOKENS = 16_384
VERIFICATION_THINKING_BUDGET = 2048
COVERAGE_THINKING_BUDGET = 2048

MAX_TRIAGE_AREAS = 12
MAX_HINTS_PER_AREA = 10


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TriageAnomaly(BaseModel):
    category: str = Field(
        description="Anomaly category — use a short descriptive label "
        "(e.g., 'Non-Running Pods')."
        "Use standard categories when they fit; create new ones for novel anomaly types."
    )
    resource_kind: str = Field(description="Kubernetes resource kind (e.g., Pod, Service, ConfigMap)")
    resource_name: str = Field(description="Name of the resource")
    namespace: str = Field(description="Namespace of the resource")
    observation: str = Field(description="Factual description of the anomaly — no interpretation")


class TriageArea(BaseModel):
    """One focus area for triage -- dispatched to a specialist subagent."""

    name: str
    hints: list[str]

    @field_validator("hints")
    @classmethod
    def max_hints(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_HINTS_PER_AREA:
            raise ValueError(f"Maximum {MAX_HINTS_PER_AREA} hints per area")
        return v


class TriagePriors(BaseModel):
    """Top-level triage priors document."""

    areas: list[TriageArea]

    @field_validator("areas")
    @classmethod
    def max_areas(cls, v: list[TriageArea]) -> list[TriageArea]:
        if len(v) > MAX_TRIAGE_AREAS:
            raise ValueError(f"Maximum {MAX_TRIAGE_AREAS} triage areas")
        return v


class TriageCoordinatorReport(BaseModel):
    cluster_snapshot: str
    """Condensed kubectl output, passed to specialists (internal only)."""
    base_anomalies: list[TriageAnomaly]
    """User-facing symptoms found during smoke test."""


class TriageSpecialistReport(BaseModel):
    category: str
    """The area name."""
    healthy: bool
    """True if no anomalies found in this area."""
    assessment: str
    """High-level summary of health in this area (1-2 sentences)."""
    anomalies: list[TriageAnomaly]
    """Empty if healthy=True."""


class AreaAssessment(BaseModel):
    category: str
    """Area name, e.g., 'Network and DNS Connectivity'."""
    assessment: str
    """1-2 sentence summary of what's wrong in this area."""


class TriageReport(BaseModel):
    anomalies: list[TriageAnomaly] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list, description="All observed anomalies, each tagged with a category"
    )
    area_assessments: list[AreaAssessment] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list
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
    confirmed_candidates: list[CandidateVerification] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list,
        description="Subset of verified_candidates where applies=True, for convenience",
    )
    novel_cause_signals: str = Field(description="What to look for if none of the candidates match")
    caveats: str = Field(default="", description="What doesn't match; what to verify before assuming patterns apply")


HypothesisCoverageVerdictLiteral = Literal["accept", "reject", "accept_partial"]


class HypothesisCoverageVerdict(BaseModel):
    """Result of checking a hypothesis against triage (full, partial, or rejected)."""

    verdict: HypothesisCoverageVerdictLiteral = Field(
        description=(
            "'accept' if every anomaly is explained or noise; "
            "'accept_partial' if the hypothesis is sound for a scoped fault but some "
            "anomalies are plausibly separate or out of scope; "
            "'reject' if the hypothesis is wrong or incomplete for what it claims"
        )
    )
    explained_anomalies: list[str] = Field(
        default_factory=list,
        description="Triage anomalies that the hypothesis explains (including pre-existing noise)",
    )
    unexplained_anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "Triage anomalies not explained by the hypothesis; may be non-empty when "
            "verdict is accept_partial (residuals that do not invalidate the hypothesis)"
        ),
    )
    residual_rationale: str = Field(
        default="",
        description=(
            "When verdict is accept_partial: why listed unexplained anomalies do not "
            "block accepting this hypothesis. Empty for accept/reject unless optional notes."
        ),
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
    lines = ["# Triage Report\n"]
    if report.area_assessments:
        lines.append("## Area Assessments\n")
        lines.extend(f"### {aa.category}\n{aa.assessment}\n" for aa in report.area_assessments)
    lines.append("## Anomalies\n")
    if not report.anomalies:
        lines.append("No anomalies detected.\n")
        return "\n".join(lines)
    lines.extend(
        f"- **[{a.category}]** `{a.resource_kind}/{a.resource_name}` (ns: {a.namespace}): {a.observation}"
        for a in report.anomalies
    )
    return "\n".join(lines)


def load_triage_priors(path: Path) -> TriagePriors:
    """Load and validate triage priors from YAML."""
    raw = yaml.safe_load(path.read_text())
    return TriagePriors.model_validate(raw)


def _subagent_middleware(trajectory_path: Path | None = None) -> list[Any]:
    """Standard middleware stack for inline subagents."""
    mw: list[Any] = [TurnLoggingMiddleware(), RetryMiddleware()]
    if trajectory_path is not None:
        mw.append(TrajectoryMiddleware(FixedPathProvider(trajectory_path)))
    return mw


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
    verification_guidance: str = "",
) -> VerifiedDifferentialDiagnosis:
    """Spawn one verification subagent per candidate in parallel and return aggregated results."""
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
            verification_guidance=verification_guidance,
        )
        logger.info("[ltm-verify-%d] PROMPT:\n%s", idx, prompt)

        verify_agent = InlineAgent(
            model_id,
            agent_name=f"ltm-verify-{idx}",
            output_type=CandidateVerification,
            tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
            model_settings=thinking_settings(model_id, VERIFICATION_THINKING_BUDGET),
            middleware=_subagent_middleware(trajectory_path),
        )
        try:
            result = await verify_agent.arun(
                prompt,
                run_ctx={"stage": stage, "role": "ltm-verify", "candidate_index": idx},
            )
            output = result.output
            # Ensure echoed fields match the candidate
            output.candidate_index = idx
            output.root_cause_class = candidate.root_cause_class
            output.root_cause = candidate.root_cause

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


SPECIALIST_THINKING_BUDGET = 1024


def _slugify(name: str) -> str:
    """Convert area name to a safe agent-name slug."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


async def triage_cluster(
    ctx: RunContext[SREDeps],
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Call this FIRST, before search_prior_incidents. Returns a structured triage
    report listing all anomalous resources (non-running pods, services without
    endpoints, misconfigurations, etc.). Pass the output to search_prior_incidents
    as part of your observed_symptoms.
    """
    model_id = ctx.deps.ltm_model_id
    if not model_id:
        return "Error: triage_cluster requires a model ID (ltm_model_id not set)."

    namespace = ctx.deps.namespace
    renderer = ctx.deps.renderer
    trajectory_path = ctx.deps.trajectory_path

    # Phase 1: Coordinator — smoke test only
    coordinator = InlineAgent(
        model_id,
        agent_name="triage-coordinator",
        output_type=TriageCoordinatorReport,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(trajectory_path),
    )

    @coordinator.agent.output_validator
    def _require_tool_calls(ctx: RunContext[None], report: TriageCoordinatorReport) -> TriageCoordinatorReport:  # pyright: ignore[reportUnusedFunction]
        from pydantic_ai.messages import ToolCallPart

        has_calls = any(isinstance(part, ToolCallPart) for msg in ctx.messages for part in msg.parts)
        if not has_calls:
            raise ModelRetry(
                "You MUST use exec_bash_any to run kubectl commands before "
                "producing the triage report. You have not called any tools yet. "
                "Run the recommended kubectl commands now."
            )
        return report

    coordinator_prompt = renderer.render(
        "triage_coordinator",
        namespace=namespace,
    )
    logger.info("[triage-coordinator] PROMPT:\n%s", coordinator_prompt)

    try:
        coord_result = await coordinator.arun(
            coordinator_prompt,
            run_ctx={"stage": ctx.deps.stage, "role": "triage-coordinator"},
        )
        coord_report = coord_result.output
    except Exception as e:
        logger.warning("[triage-coordinator] failed: %s", e)
        return f"Triage failed with error: {e}. Proceed with manual investigation."

    # Phase 2: Check triage priors
    priors = ctx.deps.triage_priors

    if not priors or not priors.areas:
        # No priors yet — return coordinator results as-is
        report = TriageReport(anomalies=list(coord_report.base_anomalies))
        ctx.deps.triage_report = report
        formatted = format_triage_report(report)
        logger.info("[triage] done (no priors): %s", formatted)
        if ctx.deps.stage_outputs_file:
            with open(ctx.deps.stage_outputs_file, "a") as f:
                f.write(f"\n## Triage Report\n{formatted}\n")
        return formatted

    # Phase 3: Parallel specialist subagents
    async def _run_specialist(area: TriageArea) -> TriageSpecialistReport:
        hints_text = "\n".join(f"- {h}" for h in area.hints)
        prompt = renderer.render(
            "triage_specialist",
            namespace=namespace,
            category=area.name,
            hints=hints_text,
            cluster_snapshot=coord_report.cluster_snapshot,
        )
        slug = _slugify(area.name)
        logger.info("[triage-%s] PROMPT:\n%s", slug, prompt)
        agent = InlineAgent(
            model_id,
            agent_name=f"triage-{slug}",
            output_type=TriageSpecialistReport,
            tools=[read_file, exec_bash_any, grep, write_file],
            model_settings=thinking_settings(model_id, SPECIALIST_THINKING_BUDGET),
            middleware=_subagent_middleware(trajectory_path),
        )
        result = await agent.arun(
            prompt,
            run_ctx={"stage": ctx.deps.stage, "role": f"triage-{slug}"},
        )
        return result.output

    sem = asyncio.Semaphore(6)

    async def _run_specialist_limited(area: TriageArea) -> TriageSpecialistReport:
        async with sem:
            try:
                return await _run_specialist(area)
            except Exception as e:
                logger.warning("[triage-%s] failed: %s", _slugify(area.name), e)
                return TriageSpecialistReport(
                    category=area.name,
                    healthy=True,
                    assessment=f"Specialist failed with error: {e}",
                    anomalies=[],
                )

    specialist_results = await asyncio.gather(
        *[_run_specialist_limited(area) for area in priors.areas],
    )

    # Phase 4: Merge — only include unhealthy areas
    unhealthy = [spec for spec in specialist_results if not spec.healthy]

    all_anomalies = list(coord_report.base_anomalies)
    for spec in unhealthy:
        all_anomalies.extend(spec.anomalies)

    # Deduplicate
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[TriageAnomaly] = []
    for a in all_anomalies:
        key = (a.resource_kind, a.resource_name, a.namespace, a.observation[:80])
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    report = TriageReport(
        anomalies=deduped,
        area_assessments=[AreaAssessment(category=spec.category, assessment=spec.assessment) for spec in unhealthy],
    )
    ctx.deps.triage_report = report
    formatted = format_triage_report(report)
    logger.info("[triage] done: %s", formatted)
    if ctx.deps.stage_outputs_file:
        with open(ctx.deps.stage_outputs_file, "a") as f:
            f.write(f"\n## Triage Report\n{formatted}\n")
    return formatted


async def check_hypothesis_coverage(
    ctx: RunContext[SREDeps],
    hypothesis: str,
) -> str:
    """Cross-check your hypothesis against the triage report (multi-fault aware).

    Call this BEFORE submitting your diagnosis. Pass your proposed root cause
    (resource, misconfigured field, causal chain). Returns structured JSON:
    `accept` when every anomaly is explained or noise; `accept_partial` when the
    hypothesis is sound for a scoped fault but some triage lines are plausibly
    separate faults or out of scope (see `residual_rationale`); `reject` when the
    hypothesis is wrong or incomplete for what it claims. If rejected, revise or
    narrow scope before resubmitting.
    """
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
    )
    logger.info("[hypothesis-coverage] PROMPT:\n%s", prompt)

    coverage_agent = InlineAgent(
        model_id,
        agent_name="hypothesis-coverage",
        output_type=HypothesisCoverageVerdict,
        model_settings=thinking_settings(model_id, COVERAGE_THINKING_BUDGET),
        middleware=_subagent_middleware(ctx.deps.trajectory_path),
    )

    try:
        result = await coverage_agent.arun(
            prompt,
            run_ctx={"stage": ctx.deps.stage, "role": "hypothesis-coverage"},
        )
        output = result.output

        output_json = output.model_dump_json(indent=2)
        logger.info(
            "[hypothesis-coverage] done: verdict=%s, unexplained=%s, residual_rationale=%s, reasoning=%s",
            output.verdict,
            output.unexplained_anomalies,
            output.residual_rationale,
            output.reasoning,
        )
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

    retrieval_agent = InlineAgent(
        ltm_model_id,
        agent_name="ltm-search",
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(ltm_model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(),
    )
    retrieval_result = await retrieval_agent.arun(prompt)
    diagnosis = retrieval_result.output
    retrieval_json = diagnosis.model_dump_json(indent=2)
    logger.info("[ltm-search] retrieval output: %s", retrieval_json)

    # Write retrieval candidates to stage outputs
    if ctx.deps.stage_outputs_file:
        with open(ctx.deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Retrieval Candidates\n**Query:** {observed_symptoms}\n\n")
            if diagnosis.candidate_root_causes:
                f.write(f"{retrieval_json}\n")
            else:
                f.write("No candidates found.\n")

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
        verification_guidance=ctx.deps.verification_guidance,
    )
    output_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-search] verified output: %s", output_json)

    # Write verification results to stage outputs
    if ctx.deps.stage_outputs_file:
        with open(ctx.deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Verification Results\n{output_json}\n")
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

    prompt = ctx.deps.renderer.render(
        "search_prior_mitigations",
        root_cause=root_cause,
        failed_attempts=failed_attempts,
        lt_summary_file=str(ctx.deps.lt_summary_file),
        incidents_dir=str(ctx.deps.incidents_dir) if ctx.deps.incidents_dir else "",
    )
    logger.info("[ltm-mitigation] PROMPT:\n%s", prompt)

    retrieval_agent = InlineAgent(
        ltm_model_id,
        agent_name="ltm-mitigation",
        output_type=MitigationSearchResult,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(ltm_model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(),
    )
    retrieval_result = await retrieval_agent.arun(prompt)
    output = retrieval_result.output
    output_json = output.model_dump_json(indent=2)
    logger.info("[ltm-mitigation] output: %s", output_json)
    if ctx.deps.stage_outputs_file:
        with open(ctx.deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Mitigation Retrieval Results\n**Query:** {root_cause}\n\n{output_json}\n")
    return output_json
