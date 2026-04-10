"""Knowledge-base retrieval tools for the Crucible SRE agent pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import ModelRetry, RunContext

from libs.agent_mw import FixedPathProvider, RetryMiddleware, TrajectoryMiddleware, TurnLoggingMiddleware
from libs.pydantic_agent import InlineAgent, UsageCollector, thinking_settings
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


class LTMMitigationShortCircuit(BaseException):
    """Short-circuit signal raised by ``search_prior_mitigations`` when the
    mitigation phase has successfully applied at least one strategy AND the
    feature flag ``enable_ltm_verified_direct_submit`` is set on ``SREDeps``.

    Mirrors :class:`LTMShortCircuit` for the diagnosis side. Inherits from
    ``BaseException`` (not ``Exception``) so it bypasses pydantic-ai middleware
    retry logic and propagates cleanly out of the SRE main agent loop to the
    orchestrator. The orchestrator catches this signal, submits ``applied``
    directly to the benchmark, and treats the iteration as APPROVED.
    """

    def __init__(self, applied: list[str], iteration: int) -> None:
        super().__init__(f"LTM mitigation short-circuit: {len(applied)} applied")
        self.applied = applied
        self.iteration = iteration


def _format_applied_mitigations_md(
    verified: VerifiedMitigationSearchResult,
    iteration: int,
) -> str:
    """Markdown summary of every mitigation strategy that went through the LTM
    mitigation phase. Used to log to the shared session file every time
    ``search_prior_mitigations`` runs."""
    if not verified.applications:
        return (
            f"\n### Iteration {iteration} — LTM Mitigation Phase\n"
            f"No mitigation playbooks matched the retrieved strategies.\n"
        )
    successful = verified.successful_applications
    lines = [
        f"\n### Iteration {iteration} — LTM Mitigation Phase",
        (f"Applied {len(verified.applications)} strategy(ies) ({len(successful)} successful):"),
        "",
    ]
    for app in verified.applications:
        marker = "✅ APPLIED" if app.applied else "❌ failed"
        lines.append(f"- **[{marker}] {app.root_cause_class}**")
        if app.applied and app.mitigation_summary:
            lines.append(f"  - summary: {app.mitigation_summary}")
        if app.reasoning:
            lines.append(f"  - reasoning: {app.reasoning}")
        if app.applied and app.verification_evidence:
            lines.extend(f"  - evidence: {ev}" for ev in app.verification_evidence)
    lines.append("")
    return "\n".join(lines)


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


def _attach_slugs_to_candidates(
    candidates: list[CandidateRootCause],
    lt_summary_file: Path | None,
) -> None:
    """Mutate ``candidates`` to attach a ``slug`` from the long-term summary if matched.

    Performs an exact-match against ``### Root Cause:`` headers, then a slug-derived
    fuzzy match (slugify both sides) for minor wording differences.
    """
    if not lt_summary_file or not lt_summary_file.exists():
        return
    try:
        from sregym_agents.crucible.knowledge_base.merge_result import extract_class_slugs
        from sregym_agents.crucible.knowledge_base.playbook import slugify
    except Exception as exc:
        logger.warning("Slug attachment skipped: %s", exc)
        return

    try:
        summary_text = lt_summary_file.read_text()
    except OSError as exc:
        logger.warning("Slug attachment: failed to read %s: %s", lt_summary_file, exc)
        return

    class_slugs = extract_class_slugs(summary_text)
    if not class_slugs:
        return

    fuzzy_index: dict[str, str] = {}
    for class_name, slug in class_slugs.items():
        fuzzy_index.setdefault(slugify(class_name), slug)

    for candidate in candidates:
        if candidate.root_cause_class in class_slugs:
            candidate.slug = class_slugs[candidate.root_cause_class]
            continue
        derived = slugify(candidate.root_cause_class)
        if derived in fuzzy_index:
            candidate.slug = fuzzy_index[derived]


def _load_playbook_text(playbooks_dir: Path | None, slug: str | None) -> str:
    """Load playbook markdown for ``slug`` (alias-aware). Returns empty string on miss."""
    if not playbooks_dir or not slug:
        return ""
    try:
        from sregym_agents.crucible.knowledge_base.playbook import PlaybookStore

        store = PlaybookStore(playbooks_dir)
        playbook = store.load(slug)
    except Exception as exc:
        logger.warning("Failed to load playbook for slug %s: %s", slug, exc)
        return ""
    if playbook is None:
        return ""
    return playbook.to_markdown()


def _resolve_strategy_slug(
    root_cause_class: str,
    lt_summary_file: Path | None,
) -> str | None:
    """Resolve a mitigation strategy's root_cause_class to a long-term-summary slug.

    Mirrors :func:`_attach_slugs_to_candidates`'s exact-then-fuzzy match. Returns
    ``None`` if no match is found.
    """
    if not lt_summary_file or not lt_summary_file.exists():
        return None
    try:
        from sregym_agents.crucible.knowledge_base.merge_result import extract_class_slugs
        from sregym_agents.crucible.knowledge_base.playbook import slugify
    except Exception as exc:
        logger.warning("Strategy slug resolution skipped: %s", exc)
        return None

    try:
        summary_text = lt_summary_file.read_text()
    except OSError as exc:
        logger.warning("Strategy slug resolution: failed to read %s: %s", lt_summary_file, exc)
        return None

    class_slugs = extract_class_slugs(summary_text)
    if not class_slugs:
        return None

    if root_cause_class in class_slugs:
        return class_slugs[root_cause_class]

    fuzzy_index: dict[str, str] = {}
    for class_name, slug in class_slugs.items():
        fuzzy_index.setdefault(slugify(class_name), slug)

    derived = slugify(root_cause_class)
    return fuzzy_index.get(derived)


def load_mitigation_playbook_text(
    mitigation_playbooks_dir: Path | None,
    slug: str | None,
) -> str:
    """Load mitigation playbook markdown for ``slug`` (alias-aware).

    Returns empty string on miss.
    """
    if not mitigation_playbooks_dir or not slug:
        return ""
    try:
        from sregym_agents.crucible.knowledge_base.mitigation_playbook import (
            MitigationPlaybookStore,
        )

        store = MitigationPlaybookStore(mitigation_playbooks_dir)
        playbook = store.load(slug)
    except Exception as exc:
        logger.warning("Failed to load mitigation playbook for slug %s: %s", slug, exc)
        return ""
    if playbook is None:
        return ""
    return playbook.to_markdown()


async def run_single_mitigation_playbook(
    playbook_text: str,
    root_cause_class: str,
    namespace: str,
    model_id: Model | str,
    renderer: PromptRenderer,
    trajectory_path: Path | None = None,
    usage_collector: UsageCollector | None = None,
    agent_name: str = "ltm-mitigate-0",
    failed_attempts: str = "",
) -> MitigationApplication:
    """Run a single mitigation playbook via an inline subagent.

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
    )
    logger.info("[%s] PROMPT:\n%s", agent_name, prompt)

    mitigate_agent = InlineAgent(
        model_id,
        agent_name=agent_name,
        output_type=MitigationApplication,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(model_id, VERIFICATION_THINKING_BUDGET),
        middleware=_subagent_middleware(trajectory_path),
        usage_collector=usage_collector,
    )
    result = await mitigate_agent.arun(
        prompt,
        run_ctx={"stage": "mitigation", "role": "ltm-mitigate", "strategy_index": 0},
    )
    output = result.output
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


async def _run_mitigation_phase(
    strategies: list[MitigationStrategy],
    search_result: MitigationSearchResult,
    namespace: str,
    stage: str,
    model_id: Model | str,
    renderer: PromptRenderer,
    trajectory_path: Path | None,
    mitigation_playbooks_dir: Path | None,
    lt_summary_file: Path | None,
    failed_attempts: str = "",
    usage_collector: UsageCollector | None = None,
) -> VerifiedMitigationSearchResult:
    """Execute mitigation playbooks for retrieved strategies, sequentially.

    For each strategy whose ``root_cause_class`` resolves to a stored mitigation
    playbook, spawn an inline subagent that:

    1. Reads the playbook
    2. Executes the Mitigation Procedure against the cluster
    3. Runs the Post-Mitigation Verification
    4. Confirms every Required Evidence checkbox is satisfied

    Strategies are attempted **sequentially** (because each subagent mutates the
    cluster) and we **early-exit on the first ``applied=True``**: once the
    cluster is fixed there is no point trying the next strategy.

    Strategies whose root_cause_class does not resolve to a playbook produce
    a ``MitigationApplication`` with ``applied=False`` and an explanatory
    ``reasoning``, but do not consume a subagent call.
    """
    applications: list[MitigationApplication] = []

    for idx, strategy in enumerate(strategies):
        slug = _resolve_strategy_slug(strategy.root_cause_class, lt_summary_file)
        playbook_text = load_mitigation_playbook_text(mitigation_playbooks_dir, slug)
        if not playbook_text:
            applications.append(
                MitigationApplication(
                    strategy_index=idx,
                    root_cause_class=strategy.root_cause_class,
                    applied=False,
                    reasoning=(
                        f"No mitigation playbook found for root_cause_class "
                        f"'{strategy.root_cause_class}' (slug={slug!r})."
                    ),
                )
            )
            continue

        try:
            output = await run_single_mitigation_playbook(
                playbook_text=playbook_text,
                root_cause_class=strategy.root_cause_class,
                namespace=namespace,
                model_id=model_id,
                renderer=renderer,
                trajectory_path=trajectory_path,
                usage_collector=usage_collector,
                agent_name=f"ltm-mitigate-{idx}",
                failed_attempts=failed_attempts,
            )
            output.strategy_index = idx
        except Exception as e:
            logger.warning("[ltm-mitigate-%d] failed: %s", idx, e)
            output = MitigationApplication(
                strategy_index=idx,
                root_cause_class=strategy.root_cause_class,
                applied=False,
                reasoning=f"Mitigation subagent raised: {e}",
            )

        applications.append(output)
        if output.applied:
            # Cluster is fixed; no point trying further strategies.
            break

    successful = [a for a in applications if a.applied]
    return VerifiedMitigationSearchResult(
        strategies=list(strategies),
        applications=applications,
        successful_applications=successful,
        novel_cause=search_result.novel_cause,
        general_guidance=search_result.general_guidance,
    )


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
    playbooks_dir: Path | None = None,
    usage_collector: UsageCollector | None = None,
) -> VerifiedDifferentialDiagnosis:
    """Spawn one verification subagent per candidate in parallel and return aggregated results."""
    triage_context = ""
    if triage_report is not None:
        triage_context = format_triage_report(triage_report)

    async def _verify_one(idx: int, candidate: CandidateRootCause) -> CandidateVerification:
        playbook_text = _load_playbook_text(playbooks_dir, candidate.slug)
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

        verify_agent = InlineAgent(
            model_id,
            agent_name=f"ltm-verify-{idx}",
            output_type=CandidateVerification,
            tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
            model_settings=thinking_settings(model_id, VERIFICATION_THINKING_BUDGET),
            middleware=_subagent_middleware(trajectory_path),
            usage_collector=usage_collector,
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


async def triage_cluster_impl(
    deps: SREDeps,
) -> str:
    """Systematically audit the Kubernetes namespace for unhealthy components.

    Args:
        deps: SRE dependency context.

    Returns a structured triage report listing all anomalous resources.
    """
    model_id = deps.model_id
    namespace = deps.namespace
    renderer = deps.renderer
    trajectory_path = deps.trajectory_path

    # Phase 1: Coordinator — smoke test only
    coordinator = InlineAgent(
        model_id,
        agent_name="triage-coordinator",
        output_type=TriageCoordinatorReport,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(trajectory_path),
        usage_collector=deps.usage_collector,
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
            run_ctx={"stage": deps.stage, "role": "triage-coordinator"},
        )
        coord_report = coord_result.output
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
        agent = InlineAgent(
            model_id,
            agent_name=f"triage-{slug}",
            output_type=TriageSpecialistReport,
            tools=[read_file, exec_bash_any, grep, write_file],
            model_settings=thinking_settings(model_id, SPECIALIST_THINKING_BUDGET),
            middleware=_subagent_middleware(trajectory_path),
            usage_collector=deps.usage_collector,
        )
        result = await agent.arun(
            prompt,
            run_ctx={"stage": deps.stage, "role": f"triage-{slug}"},
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

    model_id = deps.model_id
    triage_context = format_triage_report(triage_report)
    prompt = deps.renderer.render(
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
        middleware=_subagent_middleware(deps.trajectory_path),
        usage_collector=deps.usage_collector,
    )

    try:
        result = await coverage_agent.arun(
            prompt,
            run_ctx={"stage": deps.stage, "role": "hypothesis-coverage"},
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
    """Search past incidents and return verified candidate root causes.

    Args:
        deps: SRE dependency context.
        observed_symptoms: Factual description of current observations or hypothesis.
    """
    empty_result = (
        '{"verified_candidates": [], "confirmed_candidates": [],'
        ' "novel_cause_signals": "", "caveats": "No incident history available."}'
    )
    if not deps.lt_summary_file:
        logger.info("[ltm-search] skipped (no summary file): %s", empty_result)
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

    model_id = deps.model_id
    triage_context = ""
    if deps.triage_report is not None:
        triage_context = format_triage_report(deps.triage_report)

    prompt = deps.renderer.render(
        "search_prior_incidents",
        stage=deps.stage,
        observed_symptoms=observed_symptoms,
        triage_context=triage_context,
        lt_summary_file=str(deps.lt_summary_file),
        incidents_dir=str(deps.incidents_dir) if deps.incidents_dir else "",
    )
    logger.info("[ltm-search] PROMPT:\n%s", prompt)

    retrieval_agent = InlineAgent(
        model_id,
        agent_name="ltm-search",
        output_type=DifferentialDiagnosis,
        tools=[read_file, exec_bash_any, grep, write_file, str_replace_file],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(),
        usage_collector=deps.usage_collector,
    )
    retrieval_result = await retrieval_agent.arun(prompt)
    diagnosis = retrieval_result.output
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
        _attach_slugs_to_candidates(diagnosis.candidate_root_causes, deps.lt_summary_file)

        verified = await _run_verification_phase(
            candidates=diagnosis.candidate_root_causes,
            diagnosis=diagnosis,
            observed_symptoms=observed_symptoms,
            namespace=deps.namespace,
            stage=deps.stage,
            model_id=model_id,
            renderer=deps.renderer,
            trajectory_path=deps.trajectory_path,
            triage_report=deps.triage_report,
            verification_guidance=deps.verification_guidance,
            playbooks_dir=deps.playbooks_dir,
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


async def search_prior_mitigations_impl(
    deps: SREDeps,
    root_cause: str,
    failed_attempts: str = "",
) -> str:
    """Search past incidents for mitigation strategies matching a confirmed root cause.

    Args:
        deps: SRE dependency context.
        root_cause: The confirmed root cause diagnosis to find mitigations for.
        failed_attempts: Description of mitigation attempts that already failed (optional).
    """
    empty_result = '{"strategies": [], "novel_cause": false, "general_guidance": "No incident history available."}'
    if not deps.lt_summary_file:
        logger.info("[ltm-mitigation] skipped (no summary file): %s", empty_result)
        return empty_result

    if not root_cause.strip():
        result = "Error: root_cause must not be empty."
        logger.info("[ltm-mitigation] skipped (empty root_cause): %s", result)
        return result

    if deps.ltm_call_count >= deps.ltm_call_budget:
        result = (
            '{"strategies": [], "novel_cause": false,'
            ' "general_guidance": "Search budget exhausted. Proceed with independent mitigation."}'
        )
        logger.info("[ltm-mitigation] skipped (budget exhausted): %s", result)
        return result
    deps.ltm_call_count += 1

    model_id = deps.model_id
    prompt = deps.renderer.render(
        "search_prior_mitigations",
        root_cause=root_cause,
        failed_attempts=failed_attempts,
        lt_summary_file=str(deps.lt_summary_file),
        incidents_dir=str(deps.incidents_dir) if deps.incidents_dir else "",
    )
    logger.info("[ltm-mitigation] PROMPT:\n%s", prompt)

    retrieval_agent = InlineAgent(
        model_id,
        agent_name="ltm-mitigation",
        output_type=MitigationSearchResult,
        tools=[read_file, exec_bash_any, grep],
        model_settings=thinking_settings(model_id, THINKING_BUDGET),
        middleware=_subagent_middleware(),
        usage_collector=deps.usage_collector,
    )
    retrieval_result = await retrieval_agent.arun(prompt)
    output = retrieval_result.output
    output_json = output.model_dump_json(indent=2)
    logger.info("[ltm-mitigation] output: %s", output_json)
    if deps.stage_outputs_file:
        with open(deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Mitigation Retrieval Results\n**Query:** {root_cause}\n\n{output_json}\n")

    # --- Mitigation phase: execute playbook-guided mitigation subagents ---
    if not deps.mitigation_playbooks_dir or not output.strategies:
        return output_json

    verified = await _run_mitigation_phase(
        strategies=output.strategies,
        search_result=output,
        namespace=deps.namespace,
        stage=deps.stage,
        model_id=model_id,
        renderer=deps.renderer,
        trajectory_path=deps.trajectory_path,
        mitigation_playbooks_dir=deps.mitigation_playbooks_dir,
        lt_summary_file=deps.lt_summary_file,
        failed_attempts=failed_attempts,
        usage_collector=deps.usage_collector,
    )

    verified_json = verified.model_dump_json(indent=2)
    logger.info("[ltm-mitigation] verified output: %s", verified_json)

    # Log the mitigation phase results to the shared session file.
    try:
        deps.shared_file.append(_format_applied_mitigations_md(verified, deps.iteration))
    except Exception as e:
        logger.warning("[ltm-mitigation] failed to append phase results to shared file: %s", e)

    if deps.stage_outputs_file:
        with open(deps.stage_outputs_file, "a") as f:
            f.write(f"\n## KB Mitigation Phase Results\n{verified_json}\n")

    # Short-circuit: if the flag is on and at least one strategy applied.
    if deps.enable_ltm_verified_direct_submit and verified.successful_applications:
        applied_summaries = [a.mitigation_summary for a in verified.successful_applications]
        logger.info(
            "[ltm-mitigation] short-circuit fired with %d applied strategy(ies); raising LTMMitigationShortCircuit",
            len(applied_summaries),
        )
        raise LTMMitigationShortCircuit(applied=applied_summaries, iteration=deps.iteration)

    return verified_json


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
    return await search_prior_mitigations_impl(ctx.deps, root_cause, failed_attempts)
