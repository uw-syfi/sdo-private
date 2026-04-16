"""Knowledge-base retrieval tools for the Crucible SRE agent pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import RunContext  # noqa: TC002 — needed at runtime for pydantic-ai tool introspection

from libs.pydantic_agent import UsageCollector, thinking_settings
from sregym_agents.crucible._prompts import (
    PromptRenderer,  # noqa: TC001 — needed at runtime for pydantic-ai tool introspection
)
from sregym_agents.crucible.tools._bash_tools import exec_bash_any, grep, read_file, str_replace_file, write_file
from sregym_agents.crucible.tools._deps import (
    SREDeps,  # noqa: TC001 — needed at runtime for pydantic-ai tool introspection
)

if TYPE_CHECKING:
    from pathlib import Path

    from sregym_agents.crucible.agents.base import RunSubagent
    from sregym_agents.crucible.knowledge_base.root_cause import DiagnosisFrontMatter, KBView

import yaml

logger = logging.getLogger(__name__)

THINKING_BUDGET = 4096
MAX_OUTPUT_TOKENS = 16_384
VERIFICATION_THINKING_BUDGET = 2048
COVERAGE_THINKING_BUDGET = 2048

MAX_TRIAGE_AREAS = 12
MAX_HINTS_PER_AREA = 10
MAX_HINT_WORDS = 24
MAX_HINT_TOTAL_WORDS = 120


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
        total_words = 0
        for hint in v:
            words = len(hint.split())
            if words == 0:
                raise ValueError("Hints must not be empty")
            if words > MAX_HINT_WORDS:
                raise ValueError(f"Each hint must be at most {MAX_HINT_WORDS} words")
            total_words += words
        if total_words > MAX_HINT_TOTAL_WORDS:
            raise ValueError(f"Maximum {MAX_HINT_TOTAL_WORDS} total hint words per area")
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
    slug: str | None = Field(
        default=None,
        description=(
            "Sticky slug identifier of this root cause class as recorded in the long-term"
            " summary. Populated by search_prior_incidents when a matching playbook exists."
        ),
    )


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


class LTMShortCircuit(BaseException):
    """Short-circuit signal raised by ``search_prior_incidents`` when LTM
    verification has confirmed at least one candidate root cause and the
    feature flag ``enable_ltm_verified_direct_submit`` is set on
    ``SREDeps``.

    Inherits ``BaseException`` (not ``Exception``) so it bypasses
    pydantic-ai middleware retry logic (which catches ``Exception``) and
    propagates cleanly out of the SRE main agent loop to the orchestrator.
    The orchestrator catches this signal, submits ``confirmed`` directly to
    the benchmark, and treats the iteration as APPROVED.
    """

    def __init__(self, confirmed: list[str], iteration: int, confirmed_slugs: list[str] | None = None) -> None:
        super().__init__(f"LTM short-circuit: {len(confirmed)} confirmed candidate(s)")
        self.confirmed = confirmed
        self.iteration = iteration
        self.confirmed_slugs: list[str] = confirmed_slugs or []


def _format_investigated_hypotheses_md(
    verified: VerifiedDifferentialDiagnosis,
    iteration: int,
    stage: str,
) -> str:
    """Markdown summary of every candidate that went through LTM verification.

    Includes both confirmed (``applies=True``) and ruled-out (``applies=False``)
    entries with reasoning. Used to log to the shared session file every time
    ``search_prior_incidents`` runs, regardless of whether the short-circuit
    feature flag fires.
    """
    if not verified.verified_candidates:
        return (
            f"\n### Iteration {iteration} — LTM Investigated Hypotheses ({stage})\n"
            f"No candidates returned from KB retrieval.\n"
        )
    lines = [
        f"\n### Iteration {iteration} — LTM Investigated Hypotheses ({stage})",
        f"Verified {len(verified.verified_candidates)} candidate(s) ({len(verified.confirmed_candidates)} confirmed):",
        "",
    ]
    for c in verified.verified_candidates:
        marker = "✅ CONFIRMED" if c.applies else "❌ ruled out"
        lines.append(f"- **[{marker}] {c.root_cause_class}** — {c.root_cause}")
        if c.reasoning:
            lines.append(f"  - reasoning: {c.reasoning}")
        if c.applies and c.causal_chain:
            lines.append(f"  - causal chain: {c.causal_chain}")
    lines.append("")
    return "\n".join(lines)


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


class MitigationPlaybookMatch(BaseModel):
    """Result of classifying a confirmed root cause to a known playbook slug."""

    slug: str | None = Field(
        default=None,
        description="Matched diagnosis-playbook slug. Null when no confident match exists.",
    )
    root_cause: str = Field(
        default="",
        description="Canonical root-cause statement for the matched slug, or empty when no match exists.",
    )
    reasoning: str = Field(description="Why the playbook match does or does not apply.")
    confident: bool = Field(
        default=False,
        description="True only when the match is strong enough to attempt the mitigation playbook.",
    )


class MitigationApplication(BaseModel):
    """Result of executing a mitigation strategy against the cluster."""

    strategy_index: int = Field(description="Index of the strategy in the retrieved list")
    root_cause_class: str = Field(description="The strategy's root_cause_class (echoed for context)")
    applied: bool = Field(
        description=(
            "True if the mitigation procedure was executed AND the post-mitigation "
            "verification's submission gate was satisfied. False otherwise."
        )
    )
    applied_steps: list[str] = Field(
        default_factory=list,
        description="The mitigation steps actually executed by the subagent.",
    )
    verification_evidence: list[str] = Field(
        default_factory=list,
        description="Concrete tool-call output proving each Required Evidence item.",
    )
    mitigation_summary: str = Field(
        default="",
        description=(
            "1-line description of what was applied, suitable for benchmark submission. Empty when applied=False."
        ),
    )
    reasoning: str = Field(description="Explanation of why the mitigation succeeded or failed.")


class VerifiedMitigationSearchResult(BaseModel):
    """Mitigation search result enriched with per-strategy execution outcomes."""

    strategies: list[MitigationStrategy] = Field(
        description="The retrieved strategies, in their original order",
    )
    applications: list[MitigationApplication] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list,
        description="One MitigationApplication per strategy that was actually attempted.",
    )
    successful_applications: list[MitigationApplication] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list,
        description="Subset of applications where applied=True (convenience).",
    )
    novel_cause: bool = Field(default=False)
    general_guidance: str = Field(default="")


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


def _load_diagnosis_cards(kb_view: KBView | None) -> list[DiagnosisFrontMatter]:
    if kb_view is None:
        return []
    return kb_view.list_active_diagnosis_cards()


def _format_diagnosis_cards(cards: list[DiagnosisFrontMatter]) -> str:
    if not cards:
        return "(no diagnosis playbooks available)"
    parts: list[str] = []
    for card in cards:
        parts.append(f"- slug: {card.slug}")
        parts.append(f"  root_cause: {card.root_cause}")
        parts.append("  when_to_consider:")
        parts.extend(f"    - {item}" for item in card.when_to_consider)
        parts.append("  disambiguators:")
        parts.extend(f"    - {item}" for item in card.disambiguators)
    return "\n".join(parts)


def _load_diagnosis_playbook_text(kb_view: KBView | None, slug: str | None) -> str:
    """Load diagnosis playbook markdown for ``slug``. Returns empty string on miss."""
    if kb_view is None:
        return ""
    return kb_view.load_diagnosis_text(slug)


def load_mitigation_playbook_text(kb_view: KBView | None, slug: str | None) -> str:
    if kb_view is None:
        return ""
    return kb_view.load_mitigation_text(slug)


async def run_single_mitigation_playbook(
    playbook_text: str,
    root_cause_class: str,
    namespace: str,
    run_subagent: RunSubagent,
    renderer: PromptRenderer,
    model_id: Any = None,
    usage_collector: UsageCollector | None = None,
    agent_name: str = "ltm-mitigate-0",
    failed_attempts: str = "",
    diagnosis_shared_file: str = "",
    diagnosis_shared_content: str = "",
) -> MitigationApplication:
    """Run a single mitigation playbook via a subagent.

    Renders the ``ltm_apply_mitigation`` prompt with the given playbook text,
    spawns a subagent to execute it against the cluster, and returns the
    ``MitigationApplication`` result.
    """
    prompt = renderer.render(
        "ltm_apply_mitigation",
        namespace=namespace,
        stage="mitigation",
        strategy_index=0,
        root_cause_class=root_cause_class,
        mitigation_approach="",
        playbook=playbook_text,
        failed_attempts=failed_attempts,
        diagnosis_shared_file=diagnosis_shared_file,
        diagnosis_shared_content=diagnosis_shared_content,
    )
    logger.info("[%s] PROMPT:\n%s", agent_name, prompt)

    ms = dict(thinking_settings(model_id, VERIFICATION_THINKING_BUDGET)) if model_id else None
    output: MitigationApplication = await run_subagent(
        prompt=prompt,
        output_type=MitigationApplication,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        agent_name=agent_name,
        model_settings=ms,
        usage_collector=usage_collector,
    )
    output.strategy_index = 0
    output.root_cause_class = root_cause_class
    logger.info(
        "[%s] done: applied=%s, summary=%s, reasoning=%s",
        agent_name,
        output.applied,
        output.mitigation_summary,
        output.reasoning,
    )
    return output


async def search_prior_mitigations_impl(
    deps: SREDeps,
    confirmed_root_cause: str,
) -> str:
    """Match a root cause to one playbook, try it, and short-circuit on success."""
    empty_result = {
        "matched_slug": None,
        "matched_root_cause": "",
        "playbook_found": False,
        "playbook_attempted": False,
        "playbook_applied": False,
        "reasoning": "No mitigation knowledge base available.",
    }
    kb_view = deps.kb_view
    if kb_view is None:
        result = json.dumps(empty_result)
        logger.info("[ltm-mitigation] skipped (no root-cause KB injected): %s", result)
        return result

    if not confirmed_root_cause.strip():
        result = "Error: confirmed_root_cause must not be empty."
        logger.info("[ltm-mitigation] skipped (empty root cause): %s", result)
        return result

    if deps.ltm_call_count >= deps.ltm_call_budget:
        result = json.dumps(
            {
                "matched_slug": None,
                "matched_root_cause": "",
                "playbook_found": False,
                "playbook_attempted": False,
                "playbook_applied": False,
                "reasoning": "Search budget exhausted. Continue with independent mitigation.",
            }
        )
        logger.info("[ltm-mitigation] skipped (budget exhausted): %s", result)
        return result
    deps.ltm_call_count += 1

    run_subagent = deps.run_subagent
    if run_subagent is None:
        logger.warning("[ltm-mitigation] run_subagent not configured; returning empty result")
        return json.dumps(empty_result)

    cards = _load_diagnosis_cards(kb_view)
    prompt = deps.renderer.render(
        "ltm_search_mitigation_playbooks",
        confirmed_root_cause=confirmed_root_cause,
        diagnosis_playbook_summaries=_format_diagnosis_cards(cards),
    )
    logger.info("[ltm-mitigation] PROMPT:\n%s", prompt)

    ms = dict(thinking_settings(deps.model_id, THINKING_BUDGET)) if deps.model_id else None
    match: MitigationPlaybookMatch = await run_subagent(
        prompt=prompt,
        output_type=MitigationPlaybookMatch,
        tools=None,
        agent_name="ltm-mitigation-search",
        model_settings=ms,
        usage_collector=deps.usage_collector,
    )

    valid_slugs = {card.slug for card in cards}
    if not match.confident or not match.slug or match.slug not in valid_slugs:
        reason = match.reasoning
        if match.slug and match.slug not in valid_slugs:
            reason = f"Classifier returned invalid slug {match.slug!r}; falling back to independent mitigation."
            logger.info("[ltm-mitigation] dropping invalid slug: %s", match.slug)
        result = {
            "matched_slug": None,
            "matched_root_cause": "",
            "playbook_found": False,
            "playbook_attempted": False,
            "playbook_applied": False,
            "reasoning": reason,
        }
        output_json = json.dumps(result, indent=2)
        logger.info("[ltm-mitigation] no confident playbook match: %s", output_json)
        deps.shared_file.append(
            "\n### KB Mitigation Retrieval\n"
            f"- Root cause query: {confirmed_root_cause}\n"
            "- Outcome: No confident match\n"
            f"- Reasoning: {reason}\n"
        )
        return output_json

    deps.shared_file.append(
        "\n### KB Mitigation Retrieval\n"
        f"- Root cause query: {confirmed_root_cause}\n"
        f"- Matched slug: {match.slug}\n"
        f"- Matched root cause: {match.root_cause}\n"
        f"- Reasoning: {match.reasoning}\n"
    )

    playbook_text = load_mitigation_playbook_text(kb_view, match.slug)
    if not playbook_text:
        result = {
            "matched_slug": match.slug,
            "matched_root_cause": match.root_cause,
            "playbook_found": False,
            "playbook_attempted": False,
            "playbook_applied": False,
            "reasoning": f"No mitigation playbook found for matched slug {match.slug!r}.",
        }
        output_json = json.dumps(result, indent=2)
        logger.info("[ltm-mitigation] no mitigation playbook for slug=%r", match.slug)
        deps.shared_file.append(
            f"\n### Playbook Shortcut (Mitigation)\n- Matched slug: {match.slug}\n- Outcome: No Playbook Found\n"
        )
        return output_json

    application = await run_single_mitigation_playbook(
        playbook_text=playbook_text,
        root_cause_class=match.root_cause,
        namespace=deps.namespace,
        run_subagent=run_subagent,
        renderer=deps.renderer,
        model_id=deps.model_id,
        usage_collector=deps.usage_collector,
        agent_name="ltm-mitigate-0",
        diagnosis_shared_file=str(deps.diagnosis_shared_file) if deps.diagnosis_shared_file is not None else "",
        diagnosis_shared_content=deps.diagnosis_shared_file.read() if deps.diagnosis_shared_file is not None else "",
    )

    deps.shared_file.append(
        "\n### Playbook Shortcut (Mitigation)\n"
        f"- Matched slug: {match.slug}\n"
        f"- Outcome: {'Applied' if application.applied else 'Not Applied'}\n"
        + (
            f"- Summary: {application.mitigation_summary}\n"
            if application.applied
            else f"- Reason: {application.reasoning}\n"
        )
    )

    if application.applied:
        raise LTMShortCircuit(
            confirmed=[application.mitigation_summary],
            iteration=deps.iteration,
            confirmed_slugs=[match.slug],
        )

    result = {
        "matched_slug": match.slug,
        "matched_root_cause": match.root_cause,
        "playbook_found": True,
        "playbook_attempted": True,
        "playbook_applied": False,
        "reasoning": application.reasoning,
    }
    output_json = json.dumps(result, indent=2)
    logger.info("[ltm-mitigation] playbook attempt did not apply: %s", output_json)
    return output_json


async def _run_verification_phase(
    candidates: list[CandidateRootCause],
    diagnosis: DifferentialDiagnosis,
    observed_symptoms: str,
    namespace: str,
    stage: str,
    run_subagent: RunSubagent,
    renderer: PromptRenderer,
    model_id: Any = None,
    triage_report: TriageReport | None = None,
    verification_guidance: str = "",
    kb_view: KBView | None = None,
    usage_collector: UsageCollector | None = None,
) -> VerifiedDifferentialDiagnosis:
    """Spawn one verification subagent per candidate in parallel and return aggregated results."""
    triage_context = ""
    if triage_report is not None:
        triage_context = format_triage_report(triage_report)

    async def _verify_one(idx: int, candidate: CandidateRootCause) -> CandidateVerification:
        playbook_text = _load_diagnosis_playbook_text(kb_view, candidate.slug)
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
            playbook=playbook_text,
        )
        logger.info("[ltm-verify-%d] PROMPT:\n%s", idx, prompt)

        ms = dict(thinking_settings(model_id, VERIFICATION_THINKING_BUDGET)) if model_id else None
        try:
            output: CandidateVerification = await run_subagent(
                prompt=prompt,
                output_type=CandidateVerification,
                tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
                agent_name=f"ltm-verify-{idx}",
                model_settings=ms,
                usage_collector=usage_collector,
            )
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


_TRIAGE_COORDINATOR_MAX_RETRIES = 2


async def triage_cluster_impl(
    deps: SREDeps,
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Args:
        deps: SRE dependency context.

    Returns a structured triage report listing all anomalous resources.
    """
    run_subagent = deps.run_subagent
    if run_subagent is None:
        return "Error: run_subagent not configured on SREDeps. Cannot run triage."

    model_id = deps.model_id
    namespace = deps.namespace
    renderer = deps.renderer

    # Phase 1: Coordinator — smoke test only
    coordinator_prompt = renderer.render(
        "triage_coordinator",
        namespace=namespace,
    )
    logger.info("[triage-coordinator] PROMPT:\n%s", coordinator_prompt)

    ms = dict(thinking_settings(model_id, THINKING_BUDGET)) if model_id else None
    try:
        coord_report: TriageCoordinatorReport | None = None
        current_prompt = coordinator_prompt
        for attempt in range(_TRIAGE_COORDINATOR_MAX_RETRIES + 1):
            coord_report = await run_subagent(
                prompt=current_prompt,
                output_type=TriageCoordinatorReport,
                tools=[read_file, exec_bash_any, grep],
                agent_name="triage-coordinator",
                model_settings=ms,
                usage_collector=deps.usage_collector,
            )
            if coord_report.cluster_snapshot.strip():
                break
            if attempt < _TRIAGE_COORDINATOR_MAX_RETRIES:
                logger.warning(
                    "[triage-coordinator] empty cluster_snapshot (attempt %d/%d), retrying",
                    attempt + 1,
                    _TRIAGE_COORDINATOR_MAX_RETRIES + 1,
                )
                current_prompt = (
                    coordinator_prompt + "\n\nYou MUST use exec_bash_any to run kubectl commands before "
                    "producing the triage report. Run the recommended kubectl commands now."
                )
        assert coord_report is not None
    except Exception as e:
        logger.warning("[triage-coordinator] failed: %s", e)
        return f"Triage failed with error: {e}. Proceed with manual investigation."

    # Phase 2: Check triage priors
    priors = deps.triage_priors

    if not priors or not priors.areas:
        # No priors yet — return coordinator results as-is
        report = TriageReport(anomalies=list(coord_report.base_anomalies))
        deps.triage_report = report
        formatted = format_triage_report(report)
        logger.info("[triage] done (no priors): %s", formatted)
        if deps.stage_outputs_file:
            with open(deps.stage_outputs_file, "a") as f:
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
        ms_spec = dict(thinking_settings(model_id, SPECIALIST_THINKING_BUDGET)) if model_id else None
        return await run_subagent(
            prompt=prompt,
            output_type=TriageSpecialistReport,
            tools=[read_file, exec_bash_any, grep, write_file],
            agent_name=f"triage-{slug}",
            model_settings=ms_spec,
            usage_collector=deps.usage_collector,
        )

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
    deps.triage_report = report
    formatted = format_triage_report(report)
    logger.info("[triage] done: %s", formatted)
    if deps.stage_outputs_file:
        with open(deps.stage_outputs_file, "a") as f:
            f.write(f"\n## Triage Report\n{formatted}\n")
    return formatted


async def triage_cluster(
    ctx: RunContext[SREDeps],
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Call this FIRST, before search_prior_incidents. Returns a structured triage
    report listing all anomalous resources (non-running pods, services without
    endpoints, misconfigurations, etc.). Pass the output to search_prior_incidents
    as part of your observed_symptoms.
    """
    return await triage_cluster_impl(ctx.deps)


async def check_hypothesis_coverage_impl(
    deps: SREDeps,
    hypothesis: str,
) -> str:
    """Cross-check a hypothesis against the triage report (multi-fault aware).

    Args:
        deps: SRE dependency context.
        hypothesis: Proposed root cause to check against triage findings.
    """
    triage_report = deps.triage_report
    if triage_report is None:
        return "Error: no triage report available. Call triage_cluster first."

    run_subagent = deps.run_subagent
    if run_subagent is None:
        return "Error: run_subagent not configured on SREDeps."

    model_id = deps.model_id
    triage_context = format_triage_report(triage_report)
    prompt = deps.renderer.render(
        "check_hypothesis_coverage",
        triage_context=triage_context,
        hypothesis=hypothesis,
    )
    logger.info("[hypothesis-coverage] PROMPT:\n%s", prompt)

    ms = dict(thinking_settings(model_id, COVERAGE_THINKING_BUDGET)) if model_id else None
    try:
        output: HypothesisCoverageVerdict = await run_subagent(
            prompt=prompt,
            output_type=HypothesisCoverageVerdict,
            agent_name="hypothesis-coverage",
            model_settings=ms,
            usage_collector=deps.usage_collector,
        )

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
    return await check_hypothesis_coverage_impl(ctx.deps, hypothesis)


async def search_prior_incidents_impl(
    deps: SREDeps,
    observed_symptoms: str,
) -> str:
    """Search diagnosis playbooks and return verified candidate root causes."""
    empty_result = (
        '{"verified_candidates": [], "confirmed_candidates": [],'
        ' "novel_cause_signals": "", "caveats": "No incident history available."}'
    )
    kb_view = deps.kb_view
    if kb_view is None:
        logger.info("[ltm-search] skipped (no root-cause KB injected): %s", empty_result)
        return empty_result

    if not observed_symptoms.strip():
        result = "Error: observed_symptoms must not be empty."
        logger.info("[ltm-search] skipped (empty symptoms): %s", result)
        return result

    if deps.ltm_call_count >= deps.ltm_call_budget:
        result = (
            '{"verified_candidates": [], "confirmed_candidates": [],'
            ' "novel_cause_signals": "",'
            ' "caveats": "Search budget exhausted. Proceed with independent investigation."}'
        )
        logger.info("[ltm-search] skipped (budget exhausted): %s", result)
        return result
    deps.ltm_call_count += 1

    run_subagent = deps.run_subagent
    if run_subagent is None:
        logger.warning("[ltm-search] run_subagent not configured; returning empty result")
        return empty_result

    model_id = deps.model_id
    triage_context = ""
    if deps.triage_report is not None:
        triage_context = format_triage_report(deps.triage_report)
    cards = _load_diagnosis_cards(kb_view)
    prompt = deps.renderer.render(
        "ltm_search_diagnosis_playbooks",
        observed_symptoms=observed_symptoms,
        triage_context=triage_context,
        diagnosis_playbook_summaries=_format_diagnosis_cards(cards),
    )
    logger.info("[ltm-search] PROMPT:\n%s", prompt)

    ms = dict(thinking_settings(model_id, THINKING_BUDGET)) if model_id else None
    diagnosis: DifferentialDiagnosis = await run_subagent(
        prompt=prompt,
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep],
        agent_name="ltm-search",
        model_settings=ms,
        usage_collector=deps.usage_collector,
    )
    valid_slugs = {card.slug for card in cards}
    filtered_candidates: list[CandidateRootCause] = []
    for candidate in diagnosis.candidate_root_causes:
        if candidate.slug and candidate.slug in valid_slugs:
            filtered_candidates.append(candidate)
        else:
            logger.info("[ltm-search] dropping candidate with invalid or missing slug: %s", candidate.root_cause_class)
    diagnosis = diagnosis.model_copy(update={"candidate_root_causes": filtered_candidates})
    retrieval_json = diagnosis.model_dump_json(indent=2)
    logger.info("[ltm-search] retrieval output: %s", retrieval_json)

    # Write retrieval candidates to stage outputs
    if deps.stage_outputs_file:
        with open(deps.stage_outputs_file, "a") as f:
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
        logger.info("[ltm-search] no candidates to verify")
    else:
        verified = await _run_verification_phase(
            candidates=diagnosis.candidate_root_causes,
            diagnosis=diagnosis,
            observed_symptoms=observed_symptoms,
            namespace=deps.namespace,
            stage=deps.stage,
            run_subagent=run_subagent,
            renderer=deps.renderer,
            model_id=model_id,
            triage_report=deps.triage_report,
            verification_guidance=deps.verification_guidance,
            kb_view=kb_view,
            usage_collector=deps.usage_collector,
        )

    output_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-search] verified output: %s", output_json)

    # Always log investigated hypotheses to the shared session file so the
    # rest of the loop and any human reader can see what KB verification
    # considered, regardless of whether short-circuit fires.
    try:
        deps.shared_file.append(_format_investigated_hypotheses_md(verified, deps.iteration, deps.stage))
    except Exception as e:
        logger.warning("[ltm-search] failed to append investigated hypotheses to shared file: %s", e)

    # Write verification results to stage outputs file (separate from shared)
    if deps.stage_outputs_file:
        with open(deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Verification Results\n{output_json}\n")

    # Short-circuit only when the flag is on AND verification confirmed something.
    if deps.enable_ltm_verified_direct_submit and verified.confirmed_candidates:
        from sregym_agents.crucible.tools._judge_tools import MAX_DIAGNOSIS_CANDIDATES

        top = verified.confirmed_candidates[:MAX_DIAGNOSIS_CANDIDATES]
        confirmed_strings = [c.root_cause for c in top]
        confirmed_slugs = [diagnosis.candidate_root_causes[c.candidate_index].slug or "" for c in top]
        logger.info(
            "[ltm-search] short-circuit fired with %d confirmed candidate(s); raising LTMShortCircuit",
            len(confirmed_strings),
        )
        raise LTMShortCircuit(
            confirmed=confirmed_strings,
            iteration=deps.iteration,
            confirmed_slugs=confirmed_slugs,
        )

    return output_json


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
    return await search_prior_incidents_impl(ctx.deps, observed_symptoms)


async def search_prior_mitigations(
    ctx: RunContext[SREDeps],
    confirmed_root_cause: str,
) -> str:
    """Classify a root cause to one mitigation playbook and try it once.

    Call this FIRST in mitigation after synthesizing your current best
    root-cause statement. The tool will attempt to match that root cause to at
    most one diagnosis playbook slug, load the paired mitigation playbook, and
    execute it via a subagent. If the playbook succeeds, the run short-circuits
    and submits directly to the benchmark. If no match is found or execution
    fails, mitigation should continue independently.
    """
    return await search_prior_mitigations_impl(ctx.deps, confirmed_root_cause)
