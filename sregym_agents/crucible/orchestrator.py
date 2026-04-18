"""Async orchestrator for the Crucible dual-agent judge loop."""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver, RunSubagent
    from sregym_agents.crucible.agents.recovery_agent import RecoveryRunResult
    from sregym_agents.crucible.knowledge_base.incident_review import DiagnosisPlaybookDraft, TriageAreaCandidate
    from sregym_agents.crucible.knowledge_base.root_cause import KBView

from libs.pydantic_agent import TokenUsage, UsageCollector
from sregym_agents.crucible._benchmark import BenchmarkResult, Stage
from sregym_agents.crucible._conductor import poll_stage
from sregym_agents.crucible.agents import (
    JudgeAgent,
    RecoveryAgent,
    ShortCircuitSignal,
    SREAgent,
    SREAgentConfig,
)
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.knowledge_base.base import InjectedKB
from sregym_agents.crucible.knowledge_base.incident_records import (
    DiagnosisRunRecord,
    MitigationRunRecord,
    RecoveryDiagnosisRunRecord,
    RecoveryMitigationRunRecord,
)
from sregym_agents.crucible.tools import (
    LTMShortCircuit,
    SharedFile,
    SharedState,
    submit_to_benchmark,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class StageLoopResult:
    """Result from a single stage's agent-judge loop."""

    approved: bool
    benchmark_block: str = ""
    agent_answer: str = ""
    agent_justification: str = ""
    agent_causal_chain: str = ""
    stage_outputs_file: Path | None = None
    confirmed_slugs: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    message_history: list[object] = dataclasses.field(default_factory=lambda: cast("list[object]", []))


# Explicit agent->phase mapping. Every agent_name that can appear in a crucible
# UsageCollector is classified here. Adding a new agent requires a decision:
# add it to one of the sets (or a prefix tuple for dynamic-suffix names) or it
# will be logged as unclassified and excluded from the per-phase totals.
_DIAGNOSIS_AGENTS: frozenset[str] = frozenset(
    {
        "hypothesis-verifier",
        "judge-diagnosis",
        "ltm-search",
        "playbook-shortcut",
        "recovery-diagnosis",
        "recovery-diagnosis-playbook",
        "recovery-triage-area-candidate",
        "sre-diagnosis",
        "success-diagnosis-playbook",
        "triage-coordinator",
    }
)
_MITIGATION_AGENTS: frozenset[str] = frozenset(
    {
        "judge-mitigation",
        "ltm-mitigate-0",
        "ltm-mitigation-search",
        "recovery-mitigation",
        "recovery-mitigation-playbook",
        "sre-mitigation",
        "success-mitigation-playbook",
    }
)
# Prefix rules for dynamic-suffix agent names (e.g. triage-<slug>, ltm-verify-<idx>).
_DIAGNOSIS_AGENT_PREFIXES: tuple[str, ...] = ("ltm-verify-", "triage-")
_MITIGATION_AGENT_PREFIXES: tuple[str, ...] = ()


def _agent_phase(agent_name: str) -> str | None:
    """Classify a crucible agent name into 'diagnosis' or 'mitigation'.

    Returns ``None`` for unknown names so the caller can warn and exclude
    them from phase totals without crashing.
    """
    if agent_name in _DIAGNOSIS_AGENTS or agent_name.startswith(_DIAGNOSIS_AGENT_PREFIXES):
        return "diagnosis"
    if agent_name in _MITIGATION_AGENTS or agent_name.startswith(_MITIGATION_AGENT_PREFIXES):
        return "mitigation"
    return None


def _build_usage_metrics(
    primary: UsageCollector,
    recovery: UsageCollector,
) -> dict[str, Any]:
    """Combine primary + recovery collectors into the per-problem usage_metrics shape.

    Schema::

        {
          "primary":    {"by_agent": {...}, "total": {...}},
          "recovery":   {"by_agent": {...}, "total": {...}},
          "total":      {"input_tokens": ..., "output_tokens": ..., "cached_input_tokens": ..., "turns": ...},
          "diagnosis":  {"input_tokens": ..., "output_tokens": ..., "cached_input_tokens": ..., "turns": ...},
          "mitigation": {"input_tokens": ..., "output_tokens": ..., "cached_input_tokens": ..., "turns": ...},
        }

    ``diagnosis`` / ``mitigation`` sum token usage across primary + recovery
    collectors, partitioned by the explicit phase mapping above. Agents whose
    phase cannot be determined are still counted in ``total`` but excluded
    from the per-phase fields (and logged as a warning).
    """
    primary_dict = primary.to_dict()
    recovery_dict = recovery.to_dict()
    grand_total = TokenUsage(**primary_dict["total"]) + TokenUsage(**recovery_dict["total"])

    phase_totals: dict[str, TokenUsage] = {"diagnosis": TokenUsage(), "mitigation": TokenUsage()}
    unknown: set[str] = set()
    for sub in (primary_dict, recovery_dict):
        for agent_name, agent_stats in sub.get("by_agent", {}).items():
            phase = _agent_phase(agent_name)
            if phase is None:
                unknown.add(agent_name)
                continue
            phase_totals[phase] = phase_totals[phase] + TokenUsage(**agent_stats["total"])
    if unknown:
        logger.warning(
            "usage_metrics: agent(s) not mapped to a phase, excluded from diagnosis/mitigation totals: %s",
            sorted(unknown),
        )

    return {
        "primary": primary_dict,
        "recovery": recovery_dict,
        "total": grand_total.to_dict(),
        "diagnosis": phase_totals["diagnosis"].to_dict(),
        "mitigation": phase_totals["mitigation"].to_dict(),
    }


def _replace_hypothesis_placeholder(
    shared_file: SharedFile,
    iteration: int,
    diagnosis: str,
    justification: str,
    causal_chain: str = "",
) -> None:
    """Replace the hypothesis placeholder with the real content after judge completes."""
    placeholder = f"\n### Iteration {iteration} — Agent Hypothesis\n[Submitted — pending judge review]\n"
    real_content = (
        f"\n### Iteration {iteration} — Agent Hypothesis\n"
        f"**Diagnosis**: {diagnosis}\n"
        f"**Justification**: {justification}\n"
    )
    if causal_chain:
        real_content += f"**Causal Chain**: {causal_chain}\n"
    shared_file.replace(placeholder, real_content, 1)


def _resolve_mitigation_playbook_identity(
    diag_result: StageLoopResult,
    diagnosis_playbook_candidate: DiagnosisPlaybookDraft | None,
) -> tuple[str, str] | None:
    """Resolve the canonical mitigation playbook slug/root-cause pair."""
    idx = _extract_matched_candidate_index(diag_result.benchmark_block)
    if idx is not None and 0 <= idx < len(diag_result.confirmed_slugs):
        slug = diag_result.confirmed_slugs[idx]
        if slug:
            root_cause = (
                diagnosis_playbook_candidate.root_cause
                if diagnosis_playbook_candidate is not None
                else diag_result.agent_answer
            )
            if root_cause.strip():
                return slug, root_cause

    if diagnosis_playbook_candidate is not None:
        return diagnosis_playbook_candidate.slug, diagnosis_playbook_candidate.root_cause

    return None


async def _run_diagnosis_recovery_if_needed(
    recovery_agent: RecoveryAgent,
    *,
    app_info: dict[str, Any],
    diag_result: StageLoopResult,
    original_diag_result: StageLoopResult,
    diagnosis_sf: SharedFile,
    recovery_collector: UsageCollector,
    recovery_stage_outputs_file: Path | None,
    crucible_config: CrucibleConfig,
) -> tuple[RecoveryRunResult | None, DiagnosisPlaybookDraft | None, TriageAreaCandidate | None]:
    """Run diagnosis recovery + playbook generation when a failed diagnosis needs grounding.

    This work is only required for recovery/KB curation. It should stay off the
    diagnosis->mitigation critical path whenever mitigation still needs to run.
    """
    diagnosis_recovery = None
    diagnosis_playbook_candidate = None
    triage_area_candidate = None

    if (
        crucible_config.include_benchmark_results
        and diag_result.benchmark_block
        and "success: False" in diag_result.benchmark_block
    ):
        diagnosis_recovery = await recovery_agent.run_diagnosis(
            app_info=app_info,
            shared_file=diagnosis_sf,
            original_answer=diag_result.agent_answer,
            benchmark_block=diag_result.benchmark_block,
            usage_collector=recovery_collector,
            original_justification=diag_result.agent_justification,
            original_causal_chain=diag_result.agent_causal_chain,
            stage_outputs_file=recovery_stage_outputs_file,
        )
        if diagnosis_recovery:
            diag_result.agent_answer = diagnosis_recovery.submission.answer
            diag_result.agent_justification = diagnosis_recovery.submission.justification
            diag_result.agent_causal_chain = diagnosis_recovery.submission.causal_chain

    if (
        diagnosis_recovery
        and diagnosis_recovery.message_history
        and crucible_config.enable_diagnosis_playbook_candidates
    ):
        diagnosis_playbook_candidate = await recovery_agent.build_diagnosis_playbook_candidate(
            app_info=app_info,
            original_answer=original_diag_result.agent_answer,
            original_justification=original_diag_result.agent_justification,
            original_causal_chain=original_diag_result.agent_causal_chain,
            grounded_answer=diag_result.agent_answer,
            grounded_justification=diag_result.agent_justification,
            grounded_causal_chain=diag_result.agent_causal_chain,
            recovery_message_history=diagnosis_recovery.message_history,
            usage_collector=recovery_collector,
            stage_outputs_file=None,
            diagnosis_oracle_reasoning=_oracle_reasoning(original_diag_result.benchmark_block or "", stage="diagnosis"),
        )

        if crucible_config.enable_triage_priors and original_diag_result.stage_outputs_file is not None:
            triage_area_candidate = await recovery_agent.build_triage_area_candidate(
                app_info=app_info,
                original_answer=original_diag_result.agent_answer,
                original_justification=original_diag_result.agent_justification,
                grounded_answer=diag_result.agent_answer,
                grounded_justification=diag_result.agent_justification,
                stage_outputs=original_diag_result.stage_outputs_file.read_text(),
                recovery_message_history=diagnosis_recovery.message_history,
                usage_collector=recovery_collector,
            )

    return diagnosis_recovery, diagnosis_playbook_candidate, triage_area_candidate


def _init_mitigation_file(
    mitigation_file: Path,
    app_info: dict[str, Any],
    benchmark_block: str,
    diagnosis_answer: str,
    diagnosis_justification: str,
    diagnosis_causal_chain: str = "",
) -> None:
    """Initialize the mitigation shared file with diagnosis results.

    Always includes the benchmark's diagnosis result.  When the benchmark
    confirms a successful diagnosis, the agent's answer is included too.
    """
    benchmark_success = "success: True" in benchmark_block

    content = (
        "# SRE Mitigation Session State\n"
        "## Session\n"
        f"- App: {app_info.get('app_name', 'unknown')} "
        f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
        "## Diagnosis Results\n"
        f"{benchmark_block}\n"
    )

    if benchmark_success and diagnosis_answer:
        diag_section = (
            f"### Agent Diagnosis\n**Diagnosis**: {diagnosis_answer}\n**Justification**: {diagnosis_justification}\n"
        )
        if diagnosis_causal_chain:
            diag_section += f"**Causal Chain**: {diagnosis_causal_chain}\n"
        content += diag_section + "\n"

    content += "## Mitigation\n"

    mitigation_file.parent.mkdir(parents=True, exist_ok=True)
    mitigation_file.write_text(content)
    logger.info(f"Initialized mitigation shared file: {mitigation_file}")


async def _get_conductor_stage() -> str | None:
    """Query the conductor for the current stage.

    Returns the stage name (e.g. ``"diagnosis"``, ``"mitigation"``, ``"done"``)
    or ``None`` on failure.
    """
    api_base = f"http://{os.getenv('API_HOSTNAME', 'localhost')}:{os.getenv('API_PORT', '8000')}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{api_base}/status", timeout=5)
            resp.raise_for_status()
            return resp.json().get("stage")
    except Exception as e:
        logger.debug("Failed to query conductor stage: %s", e)
        return None


def _read_kb_content(path: Path | None) -> str:
    """Read knowledge base file content, returning empty string if missing."""
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text().strip()
    except Exception as e:
        logger.warning(f"Failed to read KB file {path}: {e}")
        return ""


def _resolve_injected_kb(injected: InjectedKB | None) -> InjectedKB | None:
    """Resolve optional paths on an InjectedKB copy."""
    if injected is None:
        return None
    return InjectedKB(
        kb_view_dir=injected.kb_view_dir.resolve() if injected.kb_view_dir else None,
        architecture=injected.architecture.resolve() if injected.architecture else None,
        diagnosis_priors=injected.diagnosis_priors.resolve() if injected.diagnosis_priors else None,
        triage_priors=injected.triage_priors.resolve() if injected.triage_priors else None,
        arbitration_priors=injected.arbitration_priors.resolve() if injected.arbitration_priors else None,
        verification_priors=injected.verification_priors.resolve() if injected.verification_priors else None,
    )


async def _direct_submit_confirmed(
    confirmed: list[str],
    stage: str,
    iteration: int,
    shared_file: SharedFile,
    submit_mcp_url: str,
    stage_outputs_file: Path | None,
    confirmed_slugs: list[str] | None = None,
) -> StageLoopResult:
    """Direct-submission path triggered by ``LTMShortCircuit``.

    Appends a "LTM Direct Submission" block + bullet list of confirmed
    candidates / applied mitigations to the shared file, posts the list to
    the benchmark via ``submit_to_benchmark``, appends the resulting
    ``benchmark_result`` block, and returns an APPROVED ``StageLoopResult``.
    The judge agent is **not** invoked.
    """
    is_mitigation = stage == "mitigation"
    item_label = "applied mitigation(s)" if is_mitigation else "candidate root cause(s)"
    list_header = "**Applied mitigations:**" if is_mitigation else "**Confirmed candidates:**"
    what_happened = (
        f"Mitigation subagents successfully applied {len(confirmed)} {item_label} from the knowledge base."
        if is_mitigation
        else f"Verification subagents confirmed {len(confirmed)} {item_label} from the knowledge base."
    )

    bullet_list = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(confirmed))
    shared_file.append(
        f"\n### Iteration {iteration} — LTM Direct Submission ({stage})\n"
        f"{what_happened} Skipping further SRE agent reasoning and the judge "
        f"step; submitting directly.\n\n"
        f"{list_header}\n{bullet_list}\n"
    )
    stage_literal: Stage = stage if stage in ("diagnosis", "mitigation") else "diagnosis"
    try:
        bench_result = await submit_to_benchmark(submit_mcp_url, confirmed, stage)
    except Exception as e:
        bench_result = BenchmarkResult(stage=stage_literal, error=str(e))
    benchmark_block = bench_result.render()

    shared_file.append(
        f"\n### Iteration {iteration} — Judge Verdict ({stage})\n"
        f"- Status: APPROVED (LTM verified short-circuit — direct submission)\n"
        f"{benchmark_block}"
    )

    return StageLoopResult(
        approved=True,
        benchmark_block=benchmark_block,
        agent_answer=confirmed[0],
        agent_justification=f"LTM verification short-circuit ({len(confirmed)} confirmed candidate(s))",
        agent_causal_chain="",
        stage_outputs_file=stage_outputs_file,
        confirmed_slugs=confirmed_slugs or [],
        message_history=[],
    )


async def _run_stage_loop(
    sre_agent: SREAgent,
    judge_agent: JudgeAgent,
    app_info: dict[str, Any],
    stage: str,
    max_iters: int,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    injected_kb: InjectedKB | None = None,
    crucible_config: CrucibleConfig | None = None,
    diagnosis_shared_file: SharedFile | None = None,
) -> StageLoopResult:
    """Run the agent->judge loop for one stage."""
    if crucible_config is None:
        crucible_config = CrucibleConfig()
    architecture_file = injected_kb.architecture if injected_kb else None
    stage_timeout = crucible_config.stage_timeout
    stage_start = time.monotonic()
    logger.info("=" * 60)
    logger.info(f"CRUCIBLE: Starting {stage.upper()} stage (timeout={stage_timeout}s)")
    logger.info("=" * 60)

    # When LTM retrieval is enabled, don't inject summary into the SRE prompt —
    # the SRE agent will use the search_prior_incidents tool instead.
    # The judge always receives the full summary regardless of the flag.
    full_lt_summary_content = ""
    lt_summary_content = ""
    lessons_content = ""
    architecture_content = _read_kb_content(architecture_file)

    # stage_outputs_file is set on the SREAgentConfig already
    stage_outputs_file = sre_agent.config.stage_outputs_file

    last_answer = ""
    last_justification = ""
    last_causal_chain = ""

    timed_out = False
    for iteration in range(1, max_iters + 1):
        elapsed = time.monotonic() - stage_start
        if elapsed >= stage_timeout:
            logger.warning(
                f"{stage.capitalize()} stage timed out after {elapsed:.0f}s "
                f"(limit={stage_timeout}s) before iteration {iteration}."
            )
            shared_file.append(
                f"\n### {stage.capitalize()} — TIMED OUT\n"
                f"Stage exceeded the {stage_timeout}s time limit after {elapsed:.0f}s.\n"
            )
            timed_out = True
            break
        logger.info(f"--- {stage.capitalize()} iteration {iteration}/{max_iters} ---")

        # SRE agent
        shared_content = shared_file.read()
        sre_result = await sre_agent.run(
            app_info=app_info,
            stage=stage,
            iteration=iteration,
            shared_file=shared_file,
            shared_content=shared_content,
            diagnosis_shared_file=diagnosis_shared_file,
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
            lt_summary_file=str(injected_kb.kb_view_dir / "manifest.json")
            if injected_kb and injected_kb.kb_view_dir
            else "",
            usage_collector=usage_collector,
        )

        # Handle short-circuit interrupts
        if not sre_result.completed and sre_result.interrupt_data is not None:
            signal = _parse_short_circuit(sre_result.interrupt_data)
            if signal is not None:
                slugs_info = f", slugs={signal.confirmed_slugs}" if signal.confirmed_slugs else ""
                logger.info(
                    f"[{stage}] Short-circuit with {len(signal.confirmed)} confirmed — "
                    f"going straight to submission (iteration {iteration}{slugs_info})."
                )
                return await _direct_submit_confirmed(
                    confirmed=signal.confirmed,
                    stage=stage,
                    iteration=signal.iteration,
                    shared_file=shared_file,
                    submit_mcp_url=submit_mcp_url,
                    stage_outputs_file=stage_outputs_file,
                    confirmed_slugs=signal.confirmed_slugs,
                )

        # Handle failed SRE run (model error, etc.)
        if not sre_result.completed:
            logger.warning(f"[{stage}] SRE agent failed on iteration {iteration} — treating as failed iteration.")
            shared_file.append(
                f"\n### Iteration {iteration} — SRE Agent Error ({stage})\n"
                f"Agent encountered a transient API error and could not complete this iteration.\n"
            )
            continue

        # Extract state from the result (attached by SREAgent.run())
        sre_state: SharedState = sre_result.state or SharedState()
        message_history = list(sre_result.messages or [])

        # Capture the hypothesis from the SRE agent for blind judge review
        last_answer = sre_state.answer or ""
        last_justification = sre_state.answer_justification or ""
        last_causal_chain = sre_state.answer_causal_chain or ""
        hypothesis_text = ""
        if sre_state.answer:
            hypothesis_text = f"**Diagnosis**: {sre_state.answer}\n"
            if sre_state.answer_justification:
                hypothesis_text += f"**Justification**: {sre_state.answer_justification}\n"
            if sre_state.answer_causal_chain:
                hypothesis_text += f"**Causal Chain**: {sre_state.answer_causal_chain}\n"

        if not crucible_config.enable_judge:
            # Reveal the agent hypothesis in the shared file (normally the judge does this)
            if sre_state.answer:
                _replace_hypothesis_placeholder(
                    shared_file,
                    iteration,
                    sre_state.answer,
                    sre_state.answer_justification or "",
                    causal_chain=last_causal_chain,
                )

            answer = sre_state.answer or ""

            # Check if the conductor already advanced past this stage (agent-cli
            # backends may submit directly via HTTP, so the stage moves before
            # the orchestrator gets a chance to submit).
            already_submitted = False
            if not answer:
                current_stage = await _get_conductor_stage()
                if current_stage and current_stage != stage:
                    logger.info(
                        "[%s] Conductor already at stage %r — agent submitted directly. "
                        "Fetching existing results instead of re-submitting.",
                        stage,
                        current_stage,
                    )
                    already_submitted = True

            stage_literal: Stage = stage if stage in ("diagnosis", "mitigation") else "diagnosis"
            if already_submitted:
                # The agent submitted directly — don't re-submit.
                # We can't fetch results without an API endpoint, so mark it
                # as externally submitted. The conductor already graded it.
                benchmark_block = BenchmarkResult(
                    stage=stage_literal,
                    note=(
                        f"Stage '{stage}' was submitted directly by the agent "
                        f"(conductor already advanced to next stage)."
                    ),
                ).render()
            else:
                if not answer:
                    logger.warning(
                        "[%s] Submitting empty answer to benchmark (no-judge mode, iteration %d). "
                        "This likely means the SRE agent did not produce a valid answer.",
                        stage,
                        iteration,
                    )
                try:
                    bench_result = await submit_to_benchmark(submit_mcp_url, answer, stage)
                except Exception as e:
                    bench_result = BenchmarkResult(stage=stage_literal, error=str(e))
                benchmark_block = bench_result.render()

            entry = (
                f"\n### Iteration {iteration} — Judge Verdict ({stage})\n"
                f"- Status: APPROVED (no-judge mode — direct submission)\n"
            )
            shared_file.append(entry + benchmark_block)

            return StageLoopResult(
                approved=True,
                benchmark_block=benchmark_block,
                agent_answer=last_answer,
                agent_justification=last_justification,
                agent_causal_chain=last_causal_chain,
                stage_outputs_file=stage_outputs_file,
                message_history=message_history,
            )

        # Judge agent
        shared_content = shared_file.read()
        judge_result = await judge_agent.run(
            app_info=app_info,
            stage=stage,
            iteration=iteration,
            shared_file=shared_file,
            shared_content=shared_content,
            submit_mcp_url=submit_mcp_url,
            hypothesis_text=hypothesis_text,
            architecture_content=architecture_content,
            lt_summary_content=full_lt_summary_content,
            lessons_content=lessons_content,
            usage_collector=usage_collector,
        )

        # Handle failed judge run
        if not judge_result.completed:
            judge_state: SharedState = judge_result.state or SharedState()
            if not judge_state.submitted:
                logger.warning(f"[{stage}] Judge agent failed on iteration {iteration} — treating as failed iteration.")
                shared_file.append(
                    f"\n### Iteration {iteration} — Judge Agent Error ({stage})\n"
                    f"Judge encountered a transient API error and could not complete this iteration.\n"
                )
                continue

        judge_state = judge_result.state or SharedState()

        # Replace the hypothesis placeholder with real content
        if sre_state.answer:
            _replace_hypothesis_placeholder(
                shared_file,
                iteration,
                sre_state.answer,
                sre_state.answer_justification or "",
                causal_chain=last_causal_chain,
            )

        verdict = judge_state.verdict
        logger.info(f"{stage.capitalize()} iteration {iteration} verdict: {verdict!r}")

        if verdict == "APPROVED":
            logger.info(f"Judge APPROVED {stage}.")
            return StageLoopResult(
                approved=True,
                benchmark_block=judge_state.benchmark_block,
                agent_answer=last_answer,
                agent_justification=last_justification,
                agent_causal_chain=last_causal_chain,
                stage_outputs_file=stage_outputs_file,
                message_history=message_history,
            )

        logger.info(f"Judge REJECTED {stage} (iteration {iteration}). Looping...")

    if timed_out:
        logger.warning(f"{stage.capitalize()} stage failed due to timeout ({stage_timeout}s).")
    else:
        logger.warning(f"Max {stage} iterations reached without APPROVED verdict.")
    return StageLoopResult(
        approved=False,
        agent_answer=last_answer,
        agent_justification=last_justification,
        agent_causal_chain=last_causal_chain,
        stage_outputs_file=stage_outputs_file,
        message_history=[],
    )


def _oracle_reasoning(benchmark_block: str, stage: Stage = "diagnosis") -> str:
    """Extract ``oracle.reasoning`` from a rendered ``<benchmark_result>`` block."""
    parsed = BenchmarkResult.parse(benchmark_block or "", stage=stage)
    if parsed is None or parsed.oracle is None:
        return ""
    return parsed.oracle.reasoning


def _extract_matched_candidate_index(benchmark_block: str) -> int | None:
    """Extract ``matched_candidate_index`` from a benchmark oracle JSON.

    Returns the 0-based index of the first passing candidate in the submitted
    list, or ``None`` if the field is absent or the oracle cannot be parsed.
    """
    parsed = BenchmarkResult.parse(benchmark_block or "", stage="diagnosis")
    if parsed is None or parsed.oracle is None:
        return None
    return parsed.oracle.matched_candidate_index


def _parse_short_circuit(interrupt_data: Any) -> ShortCircuitSignal | None:
    """Convert interrupt_data from an AgentResult into a ShortCircuitSignal, if applicable."""
    if isinstance(interrupt_data, LTMShortCircuit):
        return ShortCircuitSignal(
            confirmed=interrupt_data.confirmed,
            iteration=interrupt_data.iteration,
            confirmed_slugs=interrupt_data.confirmed_slugs,
            stage="diagnosis",
        )
    return None


async def _try_recovery_playbook_shortcut(
    *,
    driver: AgentDriver,
    model: Any,
    app_info: dict[str, Any],
    diag_result: StageLoopResult,
    injected_kb: InjectedKB | None,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    playbook_run_subagent: RunSubagent,
    primary_collector: UsageCollector,
    recovery_collector: UsageCollector,
) -> StageLoopResult | None:
    """v3 root-cause KB does not classify recovery answers to mitigation slugs."""
    return None


async def _try_playbook_shortcut(
    run_subagent: RunSubagent,
    namespace: str,
    slug: str,
    kb_view: KBView | None,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    model_id: Any = None,
    diagnosis_shared_file: SharedFile | None = None,
) -> StageLoopResult | None:
    """Try to execute a mitigation playbook directly, bypassing the SRE agent.

    Loads the mitigation playbook for *slug*, runs it via a subagent, and on
    success submits directly to the benchmark. Returns a ``StageLoopResult``
    on success or ``None`` to signal fallback to the normal mitigation loop.
    """
    from sregym_agents.crucible.tools import (
        run_single_mitigation_playbook,
    )

    playbook_text = kb_view.load_mitigation_text(slug) if kb_view is not None else ""
    if not playbook_text:
        logger.info("[playbook-shortcut] No mitigation playbook found for slug=%r", slug)
        shared_file.append(
            f"\n### Playbook Shortcut (Mitigation)\n- Matched slug: {slug}\n- Outcome: No Playbook Found\n"
        )
        return None

    from sregym_agents.crucible.knowledge_base.root_cause import MitigationPlaybook

    pb = MitigationPlaybook.parse(playbook_text)
    root_cause_class = pb.front_matter.root_cause

    try:
        output = await run_single_mitigation_playbook(
            playbook_text=playbook_text,
            root_cause_class=root_cause_class,
            namespace=namespace,
            run_subagent=run_subagent,
            renderer=renderer,
            model_id=model_id,
            usage_collector=usage_collector,
            agent_name="playbook-shortcut",
            diagnosis_shared_file=str(diagnosis_shared_file) if diagnosis_shared_file is not None else "",
            diagnosis_shared_content=diagnosis_shared_file.read() if diagnosis_shared_file is not None else "",
        )
    except Exception as exc:
        logger.warning("[playbook-shortcut] Subagent failed: %s", exc)
        shared_file.append(
            f"\n### Playbook Shortcut (Mitigation)\n"
            f"- Matched slug: {slug}\n"
            f"- Outcome: Subagent Error\n"
            f"- Reason: {exc}\n"
        )
        return None

    shared_file.append(
        f"\n### Playbook Shortcut (Mitigation)\n"
        f"- Matched slug: {slug}\n"
        f"- Outcome: {'Applied' if output.applied else 'Not Applied'}\n"
        + (f"- Summary: {output.mitigation_summary}\n" if output.applied else f"- Reason: {output.reasoning}\n")
    )

    if not output.applied:
        return None

    return await _direct_submit_confirmed(
        confirmed=[output.mitigation_summary],
        stage="mitigation",
        iteration=0,
        shared_file=shared_file,
        submit_mcp_url=submit_mcp_url,
        stage_outputs_file=None,
        confirmed_slugs=[slug],
    )


def _build_sre_agent_config(
    injected_kb: InjectedKB | None,
    crucible_config: CrucibleConfig,
) -> SREAgentConfig:
    """Build SREAgentConfig from injected_kb and crucible_config."""
    from sregym_agents.crucible.tools import load_triage_priors

    triage_priors_file = injected_kb.triage_priors if injected_kb else None
    verification_priors_file = injected_kb.verification_priors if injected_kb else None

    # v3 priors (learned rules from reflection)
    triage_priors = None
    verification_guidance = ""
    stage_outputs_file: Path | None = None
    if crucible_config.enable_triage_priors:
        stage_outputs_file = Path("diagnosis_stage_outputs.md")
        if triage_priors_file:
            yaml_path = triage_priors_file.with_suffix(".yaml")
            if yaml_path.exists():
                triage_priors = load_triage_priors(yaml_path)
        if verification_priors_file and verification_priors_file.exists():
            verification_guidance = verification_priors_file.read_text().strip()

    return SREAgentConfig(
        config=crucible_config,
        kb_view_dir=injected_kb.kb_view_dir if injected_kb else None,
        triage_priors=triage_priors,
        verification_guidance=verification_guidance,
        stage_outputs_file=stage_outputs_file,
    )


async def run(
    model: str,
    app_info: dict[str, Any],
    problem_id: str,
    diagnosis_shared_file: Path,
    mitigation_shared_file: Path,
    planned_stages: list[str],
    submit_mcp_url: str,
    renderer: PromptRenderer,
    injected_kb: InjectedKB | None = None,
    trajectory_path: Path | None = None,
    crucible_config: CrucibleConfig | None = None,
    *,
    driver: AgentDriver,
) -> dict[str, Any]:
    """Main orchestrator: runs diagnosis (and optionally mitigation) with judge-agent loop."""
    if crucible_config is None:
        crucible_config = CrucibleConfig()

    max_diag_iters = crucible_config.max_diagnosis_iterations
    max_mit_iters = crucible_config.max_mitigation_iterations
    wait_stage_timeout = crucible_config.wait_stage_timeout

    # Two collectors per problem: primary covers diagnosis + mitigation,
    # recovery covers the post-failure recovery agents.
    primary_collector = UsageCollector()
    recovery_collector = UsageCollector()

    injected_kb = _resolve_injected_kb(injected_kb)

    # Build SREAgentConfig from injected KB + crucible config
    sre_config = _build_sre_agent_config(injected_kb, crucible_config)

    # Create role agents
    sre_agent = SREAgent(driver, model, renderer, config=sre_config)
    judge_agent_obj = JudgeAgent(driver, model, renderer)
    recovery_agent = RecoveryAgent(driver, model, renderer)

    # Create run_subagent closure for playbook shortcut
    playbook_run_subagent = sre_agent.make_run_subagent(primary_collector)

    diagnosis_sf = SharedFile(diagnosis_shared_file.resolve())
    diagnosis_header = (
        "# SRE Judged Session State\n"
        "## Session\n"
        f"- App: {app_info.get('app_name', 'unknown')} "
        f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
        "## Diagnosis\n"
    )
    diagnosis_shared_file.resolve().parent.mkdir(parents=True, exist_ok=True)
    diagnosis_sf.write_text(diagnosis_header)
    logger.info(f"Initialized diagnosis shared file: {diagnosis_sf}")

    diag_result = await _run_stage_loop(
        sre_agent,
        judge_agent_obj,
        app_info,
        "diagnosis",
        max_diag_iters,
        diagnosis_sf,
        submit_mcp_url=submit_mcp_url,
        diagnosis_shared_file=None,
        renderer=renderer,
        usage_collector=primary_collector,
        injected_kb=injected_kb,
        crucible_config=crucible_config,
    )
    original_diag_result = dataclasses.replace(diag_result)
    recovery_stage_outputs_file = (
        diag_result.stage_outputs_file.with_name("recovery_diagnosis_stage_outputs.md")
        if diag_result.stage_outputs_file is not None
        else None
    )
    if recovery_stage_outputs_file is not None:
        recovery_stage_outputs_file.unlink(missing_ok=True)
    recovery_mitigation_stage_outputs_file = None

    diagnosis_recovery = None
    diagnosis_playbook_candidate = None
    diagnosis_playbook_candidate_origin: str | None = None
    triage_area_candidate = None
    if "mitigation" not in planned_stages:
        (
            diagnosis_recovery,
            diagnosis_playbook_candidate,
            triage_area_candidate,
        ) = await _run_diagnosis_recovery_if_needed(
            recovery_agent,
            app_info=app_info,
            diag_result=diag_result,
            original_diag_result=original_diag_result,
            diagnosis_sf=diagnosis_sf,
            recovery_collector=recovery_collector,
            recovery_stage_outputs_file=recovery_stage_outputs_file,
            crucible_config=crucible_config,
        )
        if diagnosis_playbook_candidate is not None:
            diagnosis_playbook_candidate_origin = "recovery"
        elif (
            crucible_config.enable_success_playbook_candidates
            and "success: True" in (diag_result.benchmark_block or "")
            and not diag_result.confirmed_slugs
            and diag_result.message_history
        ):
            diagnosis_playbook_candidate = await recovery_agent.build_success_diagnosis_playbook_candidate(
                app_info=app_info,
                diagnosis_answer=diag_result.agent_answer,
                diagnosis_justification=diag_result.agent_justification,
                diagnosis_causal_chain=diag_result.agent_causal_chain,
                diagnosis_message_history=diag_result.message_history,
                usage_collector=primary_collector,
                stage_outputs_file=None,
            )
            if diagnosis_playbook_candidate is not None:
                diagnosis_playbook_candidate_origin = "success"
        logger.info("Diagnosis-only problem — orchestrator complete.")
        result = _build_usage_metrics(primary_collector, recovery_collector)
        sof = diag_result.stage_outputs_file
        result["stage_outputs_file"] = str(sof) if sof else None
        result["diagnosis_succeeded"] = "success: True" in (diag_result.benchmark_block or "")
        result["diagnosis_run_md"] = DiagnosisRunRecord.from_stage_outputs_file(
            problem_id=problem_id,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            diagnosis_succeeded="success: True" in (original_diag_result.benchmark_block or ""),
            agent_answer=original_diag_result.agent_answer,
            agent_justification=original_diag_result.agent_justification,
            agent_causal_chain=original_diag_result.agent_causal_chain,
            benchmark_block=original_diag_result.benchmark_block,
            stage_outputs_file=original_diag_result.stage_outputs_file,
        ).to_markdown()
        result["recovery_diagnosis_run_md"] = (
            RecoveryDiagnosisRunRecord.from_stage_outputs_file(
                problem_id=problem_id,
                app_name=app_info.get("app_name", "unknown"),
                namespace=app_info.get("namespace", "default"),
                has_recovery_diagnosis=bool(diag_result.agent_answer),
                agent_answer=diag_result.agent_answer,
                agent_justification=diag_result.agent_justification,
                agent_causal_chain=diag_result.agent_causal_chain,
                benchmark_block=diag_result.benchmark_block,
                stage_outputs_file=recovery_stage_outputs_file,
            ).to_markdown()
            if diagnosis_recovery
            else None
        )
        result["diagnosis_playbook_candidate"] = (
            diagnosis_playbook_candidate.model_dump(mode="python") if diagnosis_playbook_candidate is not None else None
        )
        result["diagnosis_playbook_candidate_origin"] = diagnosis_playbook_candidate_origin
        result["triage_area_candidate"] = (
            triage_area_candidate.model_dump(mode="python") if triage_area_candidate is not None else None
        )
        result["mitigation_run_md"] = None
        result["recovery_mitigation_run_md"] = None
        result["mitigation_playbook_candidate"] = None
        result["mitigation_playbook_candidate_origin"] = None
        return result

    _init_mitigation_file(
        mitigation_shared_file,
        app_info,
        diag_result.benchmark_block,
        diag_result.agent_answer,
        diag_result.agent_justification,
        diagnosis_causal_chain=diag_result.agent_causal_chain,
    )
    mitigation_sf = SharedFile(mitigation_shared_file.resolve())

    api_base = f"http://{os.getenv('API_HOSTNAME', 'localhost')}:{os.getenv('API_PORT', '8000')}"
    logger.info("Waiting for benchmark to reach mitigation stage...")
    await poll_stage(api_base, wait_for="mitigation", timeout=wait_stage_timeout, on_timeout="warn")

    # Playbook shortcut (gated by enable_mitigation_kb)
    mit_result: StageLoopResult | None = None
    diag_confirmed = bool(diag_result.benchmark_block) and "success: True" in diag_result.benchmark_block
    if not crucible_config.enable_mitigation_kb:
        logger.info("[playbook-shortcut] Skipped — mitigation KB disabled.")
    elif not diag_confirmed:
        mit_result = await _try_recovery_playbook_shortcut(
            driver=driver,
            model=model,
            app_info=app_info,
            diag_result=diag_result,
            injected_kb=injected_kb,
            shared_file=mitigation_sf,
            submit_mcp_url=submit_mcp_url,
            renderer=renderer,
            playbook_run_subagent=playbook_run_subagent,
            primary_collector=primary_collector,
            recovery_collector=recovery_collector,
        )
    elif not diag_result.confirmed_slugs:
        logger.info("[playbook-shortcut] Skipped — no KB slugs from diagnosis stage.")
    elif not injected_kb or injected_kb.get_view() is None:
        logger.info("[playbook-shortcut] Skipped — no KB view injected.")
    else:
        idx = _extract_matched_candidate_index(diag_result.benchmark_block)
        if idx is None or not (0 <= idx < len(diag_result.confirmed_slugs)):
            logger.info(
                "[playbook-shortcut] Skipped — matched_candidate_index=%r out of range for %d slug(s).",
                idx,
                len(diag_result.confirmed_slugs),
            )
        elif not diag_result.confirmed_slugs[idx]:
            logger.info("[playbook-shortcut] Skipped — slug at index %d is empty.", idx)
        else:
            slug = diag_result.confirmed_slugs[idx]
            logger.info(
                "[playbook-shortcut] Found playbook slug=%r (candidate_index=%d) — attempting direct mitigation.",
                slug,
                idx,
            )
            mit_result = await _try_playbook_shortcut(
                run_subagent=playbook_run_subagent,
                namespace=app_info.get("namespace", "default"),
                slug=slug,
                kb_view=injected_kb.get_view(),
                shared_file=mitigation_sf,
                diagnosis_shared_file=diagnosis_sf,
                submit_mcp_url=submit_mcp_url,
                renderer=renderer,
                usage_collector=primary_collector,
                model_id=model,
            )

    if mit_result is None:
        # Update stage_outputs_file for mitigation stage
        if sre_config.stage_outputs_file:
            sre_agent.config.stage_outputs_file = Path("mitigation_stage_outputs.md")
        mit_result = await _run_stage_loop(
            sre_agent,
            judge_agent_obj,
            app_info,
            "mitigation",
            max_mit_iters,
            mitigation_sf,
            submit_mcp_url=submit_mcp_url,
            diagnosis_shared_file=diagnosis_sf,
            renderer=renderer,
            usage_collector=primary_collector,
            injected_kb=injected_kb,
            crucible_config=crucible_config,
        )
    original_mit_result = dataclasses.replace(mit_result)
    recovery_mitigation_stage_outputs_file = (
        mit_result.stage_outputs_file.with_name("recovery_mitigation_stage_outputs.md")
        if mit_result.stage_outputs_file is not None
        else None
    )
    if recovery_mitigation_stage_outputs_file is not None:
        recovery_mitigation_stage_outputs_file.unlink(missing_ok=True)

    diagnosis_recovery, diagnosis_playbook_candidate, triage_area_candidate = await _run_diagnosis_recovery_if_needed(
        recovery_agent,
        app_info=app_info,
        diag_result=diag_result,
        original_diag_result=original_diag_result,
        diagnosis_sf=diagnosis_sf,
        recovery_collector=recovery_collector,
        recovery_stage_outputs_file=recovery_stage_outputs_file,
        crucible_config=crucible_config,
    )
    if diagnosis_playbook_candidate is not None:
        diagnosis_playbook_candidate_origin = "recovery"
    elif (
        crucible_config.enable_success_playbook_candidates
        and "success: True" in (diag_result.benchmark_block or "")
        and not diag_result.confirmed_slugs
        and diag_result.message_history
    ):
        diagnosis_playbook_candidate = await recovery_agent.build_success_diagnosis_playbook_candidate(
            app_info=app_info,
            diagnosis_answer=diag_result.agent_answer,
            diagnosis_justification=diag_result.agent_justification,
            diagnosis_causal_chain=diag_result.agent_causal_chain,
            diagnosis_message_history=diag_result.message_history,
            usage_collector=primary_collector,
            stage_outputs_file=None,
        )
        if diagnosis_playbook_candidate is not None:
            diagnosis_playbook_candidate_origin = "success"

    mitigation_recovery = None
    # Recovery mitigation
    if (
        crucible_config.include_benchmark_results
        and mit_result.benchmark_block
        and "success: False" in mit_result.benchmark_block
    ):
        mitigation_recovery = await recovery_agent.run_mitigation(
            app_info=app_info,
            shared_file=mitigation_sf,
            original_answer=mit_result.agent_answer,
            benchmark_block=mit_result.benchmark_block,
            usage_collector=recovery_collector,
            original_justification=mit_result.agent_justification,
            diagnosis_answer=diag_result.agent_answer,
            stage_outputs_file=recovery_mitigation_stage_outputs_file,
        )
        if mitigation_recovery:
            mit_result.agent_answer = mitigation_recovery.submission.answer
            mit_result.agent_justification = mitigation_recovery.submission.justification

    mitigation_playbook_candidate = None
    mitigation_playbook_candidate_origin: str | None = None
    mitigation_identity = _resolve_mitigation_playbook_identity(diag_result, diagnosis_playbook_candidate)
    if crucible_config.enable_mitigation_playbook_curation and mitigation_identity is not None:
        slug, root_cause = mitigation_identity
        diagnosis_oracle_reasoning = _oracle_reasoning(original_diag_result.benchmark_block or "", stage="diagnosis")
        if mitigation_recovery and mitigation_recovery.message_history:
            mitigation_playbook_candidate = await recovery_agent.build_mitigation_playbook_candidate(
                app_info=app_info,
                root_cause_slug=slug,
                root_cause=root_cause,
                diagnosis_answer=diag_result.agent_answer,
                original_answer=original_mit_result.agent_answer,
                original_justification=original_mit_result.agent_justification,
                grounded_answer=mit_result.agent_answer,
                grounded_justification=mit_result.agent_justification,
                recovery_message_history=mitigation_recovery.message_history,
                usage_collector=recovery_collector,
                stage_outputs_file=None,
                diagnosis_oracle_reasoning=diagnosis_oracle_reasoning,
            )
            if mitigation_playbook_candidate is not None:
                mitigation_playbook_candidate_origin = "recovery"
        elif (
            "success: True" in (mit_result.benchmark_block or "")
            and mit_result.message_history
            and not (mit_result.confirmed_slugs or [])
        ):
            mitigation_playbook_candidate = await recovery_agent.build_success_mitigation_playbook_candidate(
                app_info=app_info,
                root_cause_slug=slug,
                root_cause=root_cause,
                diagnosis_answer=diag_result.agent_answer,
                mitigation_answer=mit_result.agent_answer,
                mitigation_justification=mit_result.agent_justification,
                mitigation_message_history=mit_result.message_history,
                usage_collector=primary_collector,
                stage_outputs_file=None,
                diagnosis_oracle_reasoning=diagnosis_oracle_reasoning,
            )
            if mitigation_playbook_candidate is not None:
                mitigation_playbook_candidate_origin = "success"

    logger.info("=" * 60)
    logger.info("CRUCIBLE: Orchestrator complete.")
    logger.info("=" * 60)
    result = _build_usage_metrics(primary_collector, recovery_collector)
    sof = diag_result.stage_outputs_file or mit_result.stage_outputs_file
    result["stage_outputs_file"] = str(sof) if sof else None
    result["diagnosis_succeeded"] = "success: True" in (diag_result.benchmark_block or "")
    result["mitigation_succeeded"] = "success: True" in (mit_result.benchmark_block or "")
    result["diagnosis_run_md"] = DiagnosisRunRecord.from_stage_outputs_file(
        problem_id=problem_id,
        app_name=app_info.get("app_name", "unknown"),
        namespace=app_info.get("namespace", "default"),
        diagnosis_succeeded="success: True" in (original_diag_result.benchmark_block or ""),
        agent_answer=original_diag_result.agent_answer,
        agent_justification=original_diag_result.agent_justification,
        agent_causal_chain=original_diag_result.agent_causal_chain,
        benchmark_block=original_diag_result.benchmark_block,
        stage_outputs_file=original_diag_result.stage_outputs_file,
    ).to_markdown()
    result["recovery_diagnosis_run_md"] = (
        RecoveryDiagnosisRunRecord.from_stage_outputs_file(
            problem_id=problem_id,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            has_recovery_diagnosis=bool(diag_result.agent_answer),
            agent_answer=diag_result.agent_answer,
            agent_justification=diag_result.agent_justification,
            agent_causal_chain=diag_result.agent_causal_chain,
            benchmark_block=diag_result.benchmark_block,
            stage_outputs_file=recovery_stage_outputs_file,
        ).to_markdown()
        if diagnosis_recovery
        else None
    )
    result["diagnosis_playbook_candidate"] = (
        diagnosis_playbook_candidate.model_dump(mode="python") if diagnosis_playbook_candidate is not None else None
    )
    result["diagnosis_playbook_candidate_origin"] = diagnosis_playbook_candidate_origin
    result["triage_area_candidate"] = (
        triage_area_candidate.model_dump(mode="python") if triage_area_candidate is not None else None
    )
    result["mitigation_run_md"] = MitigationRunRecord.from_stage_outputs_file(
        problem_id=problem_id,
        app_name=app_info.get("app_name", "unknown"),
        namespace=app_info.get("namespace", "default"),
        mitigation_succeeded="success: True" in (original_mit_result.benchmark_block or ""),
        agent_answer=original_mit_result.agent_answer,
        agent_justification=original_mit_result.agent_justification,
        benchmark_block=original_mit_result.benchmark_block,
        stage_outputs_file=original_mit_result.stage_outputs_file,
    ).to_markdown()
    result["recovery_mitigation_run_md"] = (
        RecoveryMitigationRunRecord.from_stage_outputs_file(
            problem_id=problem_id,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            has_recovery_mitigation=bool(mit_result.agent_answer),
            agent_answer=mit_result.agent_answer,
            agent_justification=mit_result.agent_justification,
            benchmark_block=mit_result.benchmark_block,
            stage_outputs_file=recovery_mitigation_stage_outputs_file,
        ).to_markdown()
        if mitigation_recovery
        else None
    )
    result["mitigation_playbook_candidate"] = (
        mitigation_playbook_candidate.model_dump(mode="python") if mitigation_playbook_candidate is not None else None
    )
    result["mitigation_playbook_candidate_origin"] = mitigation_playbook_candidate_origin
    return result
