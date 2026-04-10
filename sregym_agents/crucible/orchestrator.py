"""Async orchestrator for the Crucible dual-agent judge loop."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model, infer_model

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer

from libs.agent_mw import arun_with_retry_tracked
from libs.pydantic_agent import TokenUsage, UsageCollector
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.judge_agent import CrucibleJudgeAgent
from sregym_agents.crucible.knowledge_base.base import InjectedKB
from sregym_agents.crucible.recovery_reflection import RecoveryReflection
from sregym_agents.crucible.sre_agent import CrucibleSREAgent
from sregym_agents.crucible.tools import (
    JudgeDeps,
    LTMMitigationShortCircuit,
    LTMShortCircuit,
    SharedFile,
    SharedState,
    SREDeps,
    SRESubmission,
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
    agent_reflection: str = ""
    recovery_reflection: RecoveryReflection | None = None
    stage_outputs_file: Path | None = None
    confirmed_slugs: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


def _build_usage_metrics(
    primary: UsageCollector,
    recovery: UsageCollector,
) -> dict[str, Any]:
    """Combine primary + recovery collectors into the per-problem usage_metrics shape.

    Schema::

        {
          "primary":  {"by_agent": {...}, "total": {...}},
          "recovery": {"by_agent": {...}, "total": {...}},
          "total":    {"input_tokens": ..., "output_tokens": ..., "cached_input_tokens": ..., "turns": ...}
        }
    """
    primary_dict = primary.to_dict()
    recovery_dict = recovery.to_dict()
    grand_total = TokenUsage(**primary_dict["total"]) + TokenUsage(**recovery_dict["total"])
    return {
        "primary": primary_dict,
        "recovery": recovery_dict,
        "total": grand_total.to_dict(),
    }


def _replace_hypothesis_placeholder(
    shared_file: SharedFile | Path,
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
    content = shared_file.read_text()
    if placeholder in content:
        shared_file.write_text(content.replace(placeholder, real_content, 1))


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


async def _wait_for_mitigation_stage(api_base: str, timeout: int = 300) -> None:
    """Poll until conductor reaches mitigation stage."""
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(f"{api_base}/status", timeout=5)
                resp.raise_for_status()
                stage = resp.json().get("stage")
                if stage == "mitigation":
                    return
                logger.debug(f"Stage: {stage!r}, waiting for mitigation...")
            except Exception as e:
                logger.debug(f"Status check failed: {e}")
            await asyncio.sleep(1)
    logger.warning(f"Timed out waiting for mitigation stage after {timeout}s — proceeding anyway.")


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
        summary=injected.summary.resolve() if injected.summary else None,
        lessons=injected.lessons.resolve() if injected.lessons else None,
        architecture=injected.architecture.resolve() if injected.architecture else None,
        incidents_dir=injected.incidents_dir.resolve() if injected.incidents_dir else None,
        diagnosis_priors=injected.diagnosis_priors.resolve() if injected.diagnosis_priors else None,
        triage_priors=injected.triage_priors.resolve() if injected.triage_priors else None,
        arbitration_priors=injected.arbitration_priors.resolve() if injected.arbitration_priors else None,
        verification_priors=injected.verification_priors.resolve() if injected.verification_priors else None,
        playbooks_dir=injected.playbooks_dir.resolve() if injected.playbooks_dir else None,
        mitigation_playbooks_dir=(
            injected.mitigation_playbooks_dir.resolve() if injected.mitigation_playbooks_dir else None
        ),
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
    """Direct-submission path triggered by ``LTMShortCircuit`` (diagnosis) or
    ``LTMMitigationShortCircuit`` (mitigation).

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
    try:
        success, message, oracle = await submit_to_benchmark(submit_mcp_url, confirmed, stage)
        oracle_text = f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>" if oracle is not None else ""
        benchmark_block = (
            f"\n<benchmark_result>\nsuccess: {success}\nmessage: {message}\n{oracle_text}\n</benchmark_result>\n"
        )
    except Exception as e:
        benchmark_block = f"\n<benchmark_result>\nError submitting to benchmark: {e}\n</benchmark_result>\n"

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
    )


async def _run_stage_loop(
    model: Model,
    app_info: dict[str, Any],
    stage: str,
    max_iters: int,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    injected_kb: InjectedKB | None = None,
    trajectory_path: Path | None = None,
    crucible_config: CrucibleConfig | None = None,
) -> StageLoopResult:
    """Run the agent->judge loop for one stage."""
    if crucible_config is None:
        crucible_config = CrucibleConfig()
    lt_summary_file = injected_kb.summary if injected_kb else None
    lessons_file = injected_kb.lessons if injected_kb else None
    architecture_file = injected_kb.architecture if injected_kb else None
    incidents_dir = injected_kb.incidents_dir if injected_kb else None
    triage_priors_file = injected_kb.triage_priors if injected_kb else None
    verification_priors_file = injected_kb.verification_priors if injected_kb else None
    playbooks_dir = injected_kb.playbooks_dir if injected_kb else None
    mitigation_playbooks_dir = injected_kb.mitigation_playbooks_dir if injected_kb else None
    stage_timeout = crucible_config.stage_timeout
    stage_start = time.monotonic()
    logger.info("=" * 60)
    logger.info(f"CRUCIBLE: Starting {stage.upper()} stage (timeout={stage_timeout}s)")
    logger.info("=" * 60)

    # When LTM retrieval is enabled, don't inject summary into the SRE prompt —
    # the SRE agent will use the search_prior_incidents tool instead.
    # The judge always receives the full summary regardless of the flag.
    full_lt_summary_content = _read_kb_content(lt_summary_file)
    if crucible_config.enable_ltm_retrieval:
        lt_summary_content = ""
    else:
        lt_summary_content = full_lt_summary_content
    # Lessons and architecture are always injected unconditionally.
    lessons_content = _read_kb_content(lessons_file)
    architecture_content = _read_kb_content(architecture_file)

    # v3 priors (learned rules from reflection)
    is_v3 = crucible_config.prompt_version >= "v3"
    triage_priors = None
    verification_guidance = ""
    stage_outputs_file: Path | None = None
    if is_v3:
        stage_outputs_file = Path(f"{stage}_stage_outputs.md")
        # Try YAML triage priors first, fall back to markdown file
        if triage_priors_file:
            yaml_path = triage_priors_file.with_suffix(".yaml")
            if yaml_path.exists():
                from sregym_agents.crucible.tools import load_triage_priors

                triage_priors = load_triage_priors(yaml_path)
            elif triage_priors_file.exists():
                # Legacy: read markdown and pass as-is via TriagePriors won't work,
                # so we read markdown content and build a single-area TriagePriors
                import re as _re

                from sregym_agents.crucible.tools import TriagePriors as _TP

                content = triage_priors_file.read_text().strip()
                if content:
                    areas: list[dict[str, Any]] = []
                    current_name = None
                    current_hints: list[str] = []
                    for line in content.splitlines():
                        header_match = _re.match(r"^##\s+(.+)$", line)
                        if header_match:
                            if current_name and current_hints:
                                areas.append({"name": current_name, "hints": current_hints})
                            current_name = header_match.group(1).strip()
                            current_hints = []
                        elif line.strip().startswith("- "):
                            hint = line.strip()[2:].strip()
                            if hint:
                                current_hints.append(hint)
                    if current_name and current_hints:
                        areas.append({"name": current_name, "hints": current_hints})
                    if areas:
                        triage_priors = _TP.model_validate({"areas": areas})
        if verification_priors_file and verification_priors_file.exists():
            verification_guidance = verification_priors_file.read_text().strip()

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
        sre_state = SharedState()
        sre_deps = SREDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            renderer=renderer,
            state=sre_state,
            lt_summary_file=lt_summary_file if crucible_config.enable_ltm_retrieval else None,
            incidents_dir=incidents_dir if crucible_config.enable_ltm_retrieval else None,
            playbooks_dir=playbooks_dir if crucible_config.enable_ltm_retrieval else None,
            mitigation_playbooks_dir=(mitigation_playbooks_dir if crucible_config.enable_ltm_retrieval else None),
            model_id=model,
            enable_ltm_verified_direct_submit=crucible_config.enable_ltm_verified_direct_submit,
            trajectory_path=trajectory_path,
            triage_priors=triage_priors,
            verification_guidance=verification_guidance,
            stage_outputs_file=stage_outputs_file,
            usage_collector=usage_collector,
        )
        sre_system = renderer.render(f"{stage}_agent_system")
        sre_prompt = renderer.render(
            f"{stage}_agent_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
            lt_summary_file=str(lt_summary_file) if lt_summary_file else "",
        )
        logger.info(f"[{stage}-agent] SYSTEM PROMPT:\n{sre_system}")
        logger.info(f"[{stage}-agent] USER PROMPT:\n{sre_prompt}")
        sre_agent = CrucibleSREAgent(
            model, sre_deps, trajectory_path=trajectory_path, system_prompt_override=sre_system
        )
        try:
            await sre_agent.arun(sre_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "sre"})
        except LTMShortCircuit as sig:
            # LTM verification confirmed at least one candidate; skip the rest
            # of the SRE agent and the judge entirely and submit directly.
            logger.info(
                f"[{stage}] LTM short-circuit on iteration {iteration} with "
                f"{len(sig.confirmed)} confirmed candidate(s); submitting directly."
            )
            return await _direct_submit_confirmed(
                confirmed=sig.confirmed,
                stage=stage,
                iteration=sig.iteration,
                shared_file=shared_file,
                submit_mcp_url=submit_mcp_url,
                stage_outputs_file=stage_outputs_file,
                confirmed_slugs=sig.confirmed_slugs,
            )
        except LTMMitigationShortCircuit as sig:
            # LTM mitigation phase successfully applied at least one strategy;
            # skip the rest of the SRE agent and the judge and submit directly.
            logger.info(
                f"[{stage}] LTM mitigation short-circuit on iteration {iteration} with "
                f"{len(sig.applied)} applied strategy(ies); submitting directly."
            )
            return await _direct_submit_confirmed(
                confirmed=sig.applied,
                stage=stage,
                iteration=sig.iteration,
                shared_file=shared_file,
                submit_mcp_url=submit_mcp_url,
                stage_outputs_file=stage_outputs_file,
            )
        except ModelHTTPError as exc:
            logger.warning(
                f"[{stage}] SRE agent failed with HTTP {exc.status_code} on iteration {iteration} "
                f"— treating as failed iteration."
            )
            shared_file.append(
                f"\n### Iteration {iteration} — SRE Agent Error ({stage})\n"
                f"Agent encountered a transient API error (HTTP {exc.status_code}) "
                f"and could not complete this iteration.\n"
            )
            continue

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
            try:
                success, message, oracle = await submit_to_benchmark(submit_mcp_url, answer, stage)
                oracle_text = f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>" if oracle is not None else ""
                benchmark_block = (
                    f"\n<benchmark_result>\nsuccess: {success}\nmessage: {message}\n"
                    f"{oracle_text}\n</benchmark_result>\n"
                )
            except Exception as e:
                benchmark_block = f"\n<benchmark_result>\nError submitting to benchmark: {e}\n</benchmark_result>\n"

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
            )

        # Judge agent
        shared_content = shared_file.read()
        judge_state = SharedState()
        judge_deps = JudgeDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            submit_mcp_url=submit_mcp_url,
            renderer=renderer,
            hypothesis_text=hypothesis_text,
            state=judge_state,
            usage_collector=usage_collector,
        )
        judge_system = renderer.render(f"{stage}_judge_system")
        judge_prompt = renderer.render(
            f"{stage}_judge_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
            architecture_content=architecture_content,
            lt_summary_content=full_lt_summary_content,
            lessons_content=lessons_content,
        )
        logger.info(f"[{stage}-judge] SYSTEM PROMPT:\n{judge_system}")
        logger.info(f"[{stage}-judge] USER PROMPT:\n{judge_prompt}")
        judge_agent = CrucibleJudgeAgent(model, judge_deps, trajectory_path=trajectory_path)
        try:
            await judge_agent.arun(judge_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "judge"})
        except ModelHTTPError as exc:
            logger.warning(
                f"[{stage}] Judge agent failed with HTTP {exc.status_code} on iteration {iteration} "
                f"— treating as failed iteration."
            )
            shared_file.append(
                f"\n### Iteration {iteration} — Judge Agent Error ({stage})\n"
                f"Judge encountered a transient API error (HTTP {exc.status_code}) "
                f"and could not complete this iteration.\n"
            )
            continue

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
    )


def _extract_benchmark_reasoning(benchmark_block: str, stage: str = "diagnosis") -> str:
    """Extract the 'reasoning' field from a benchmark_result block.

    Args:
        benchmark_block: Raw benchmark result block containing ``<oracle>`` tags.
        stage: ``"diagnosis"`` or ``"mitigation"`` — determines which key to
            look up inside the oracle JSON (``Diagnosis`` vs ``Mitigation``).
    """
    match = re.search(r"<oracle>\s*(.*?)\s*</oracle>", benchmark_block, re.DOTALL)
    if not match:
        return ""
    try:
        data = json.loads(match.group(1))
        key = "Mitigation" if stage == "mitigation" else "Diagnosis"
        return data.get(key, {}).get("reasoning", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _extract_matched_candidate_index(benchmark_block: str) -> int | None:
    """Extract ``matched_candidate_index`` from a benchmark oracle JSON.

    Returns the 0-based index of the first passing candidate in the submitted
    list, or ``None`` if the field is absent or the oracle cannot be parsed.
    """
    match = re.search(r"<oracle>\s*(.*?)\s*</oracle>", benchmark_block, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        idx = data.get("Diagnosis", {}).get("matched_candidate_index")
        return int(idx) if idx is not None else None
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return None


async def _try_playbook_shortcut(
    model: Model,
    namespace: str,
    slug: str,
    mitigation_playbooks_dir: Path,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    trajectory_path: Path | None = None,
) -> StageLoopResult | None:
    """Try to execute a mitigation playbook directly, bypassing the SRE agent.

    Loads the mitigation playbook for *slug*, runs it via a subagent, and on
    success submits directly to the benchmark. Returns a ``StageLoopResult``
    on success or ``None`` to signal fallback to the normal mitigation loop.
    """
    from sregym_agents.crucible.tools._kb_tools import (
        load_mitigation_playbook_text,
        run_single_mitigation_playbook,
    )

    playbook_text = load_mitigation_playbook_text(mitigation_playbooks_dir, slug)
    if not playbook_text:
        logger.info("[playbook-shortcut] No mitigation playbook found for slug=%r", slug)
        shared_file.append(
            f"\n### Playbook Shortcut (Mitigation)\n- Matched slug: {slug}\n- Outcome: No Playbook Found\n"
        )
        return None

    # Resolve class_name from the playbook for the subagent prompt.
    from sregym_agents.crucible.knowledge_base.mitigation_playbook import MitigationPlaybookStore

    store = MitigationPlaybookStore(mitigation_playbooks_dir)
    pb = store.load(slug)
    root_cause_class = pb.class_name if pb else slug

    try:
        output = await run_single_mitigation_playbook(
            playbook_text=playbook_text,
            root_cause_class=root_cause_class,
            namespace=namespace,
            model_id=model,
            renderer=renderer,
            trajectory_path=trajectory_path,
            usage_collector=usage_collector,
            agent_name="playbook-shortcut",
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
    )


async def _run_recovery_diagnosis(
    model: Model,
    app_info: dict[str, Any],
    shared_file: SharedFile,
    original_answer: str,
    benchmark_block: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    trajectory_path: Path | None = None,
    original_justification: str = "",
    original_causal_chain: str = "",
    stage_outputs_file: Path | None = None,
) -> SRESubmission | None:
    """Run a recovery diagnosis agent to produce a causal chain for the correct root cause.

    Called when ``include_benchmark_results`` is enabled and the benchmark
    rejected the agent's diagnosis.  The recovery agent receives the
    benchmark's ground-truth reasoning and investigates the cluster to build
    a validated causal chain — it does NOT resubmit to the benchmark.
    """
    reasoning = _extract_benchmark_reasoning(benchmark_block)
    if not reasoning:
        logger.warning("Recovery diagnosis: no benchmark reasoning found, skipping.")
        return None

    logger.info("=" * 60)
    logger.info("RECOVERY DIAGNOSIS: producing causal chain from benchmark ground truth")
    logger.info("=" * 60)

    # Write a separator into stage_outputs_file so the reflector can
    # distinguish primary-agent outputs from the recovery investigation.
    if stage_outputs_file:
        with open(stage_outputs_file, "a") as f:
            f.write("\n---\n## Recovery Diagnosis Investigation\n")

    sre_state = SharedState()
    sre_deps = SREDeps(
        namespace=app_info.get("namespace", "default"),
        shared_file=shared_file,
        iteration=0,  # recovery — not a regular iteration
        stage="diagnosis",
        model_id=model,
        renderer=renderer,
        state=sre_state,
        stage_outputs_file=stage_outputs_file,
        usage_collector=usage_collector,
    )

    system_prompt = renderer.render("recovery_diagnosis_system")
    user_prompt = renderer.render(
        "recovery_diagnosis_user",
        benchmark_reasoning=reasoning,
        original_answer=original_answer,
        original_justification=original_justification,
        original_causal_chain=original_causal_chain,
        app_name=app_info.get("app_name", "unknown"),
        namespace=app_info.get("namespace", "default"),
        descriptions=app_info.get("descriptions", ""),
    )
    logger.info(f"[recovery-diagnosis] SYSTEM PROMPT:\n{system_prompt}")
    logger.info(f"[recovery-diagnosis] USER PROMPT:\n{user_prompt}")

    agent = CrucibleSREAgent(model, sre_deps, trajectory_path=trajectory_path, system_prompt_override=system_prompt)
    try:
        await agent.arun(
            user_prompt,
            run_ctx={"stage": "diagnosis", "iteration": 0, "role": "recovery"},
        )
    except Exception as e:
        logger.warning(f"Recovery diagnosis agent failed: {e}")
        return None

    if not sre_state.submitted:
        logger.warning("Recovery diagnosis agent did not submit an answer.")
        return None

    submission = SRESubmission(
        answer=sre_state.answer or "",
        justification=sre_state.answer_justification or "",
        causal_chain=sre_state.answer_causal_chain or "",
        reflection=sre_state.answer_reflection or "",
        message_history=agent.last_run_messages,
    )

    # Append recovery result to shared file for KB consumption
    entry = (
        f"\n### Recovery Diagnosis\n**Diagnosis**: {submission.answer}\n**Justification**: {submission.justification}\n"
    )
    if submission.causal_chain:
        entry += f"**Causal Chain**: {submission.causal_chain}\n"
    if submission.reflection:
        entry += f"**Agent Reflection**: {submission.reflection}\n"
    try:
        shared_file.append(entry)
    except Exception as e:
        logger.warning(f"Error writing recovery diagnosis to shared file: {e}")

    # Append recovery result to stage outputs file
    if stage_outputs_file:
        with open(stage_outputs_file, "a") as f:
            f.write(f"**Diagnosis**: {submission.answer}\n")
            f.write(f"**Justification**: {submission.justification}\n")
            if submission.causal_chain:
                f.write(f"**Causal Chain**: {submission.causal_chain}\n")
            if submission.reflection:
                f.write(f"**Agent Reflection**: {submission.reflection}\n")

    logger.info(f"Recovery diagnosis complete: {submission.answer}")
    return submission


async def _run_recovery_reflection_phase(
    model: Model,
    app_info: dict[str, Any],
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    original_answer: str,
    original_justification: str = "",
    original_causal_chain: str = "",
    stage_outputs_file: Path | None = None,
    phase1_messages: list[Any] | None = None,
) -> RecoveryReflection:
    """Use grounded recovery context plus the original trajectory to produce a KB-focused reflection."""
    stage_outputs = ""
    if stage_outputs_file and stage_outputs_file.exists():
        stage_outputs = stage_outputs_file.read_text().strip()

    system_prompt = renderer.render("recovery_reflection_system")
    user_prompt = renderer.render(
        "recovery_reflection_user",
        stage_outputs=stage_outputs or "(No stage outputs captured.)",
        original_answer=original_answer,
        original_justification=original_justification,
        original_causal_chain=original_causal_chain,
        app_name=app_info.get("app_name", "unknown"),
        namespace=app_info.get("namespace", "default"),
        descriptions=app_info.get("descriptions", ""),
    )

    agent: Agent[None, RecoveryReflection] = Agent(model, output_type=RecoveryReflection)

    @agent.instructions
    def _system() -> str:  # pyright: ignore[reportUnusedFunction]
        return system_prompt

    result = await arun_with_retry_tracked(
        agent,
        user_prompt,
        agent_name="recovery-reflection",
        usage_collector=usage_collector,
        message_history=phase1_messages or [],
    )
    reflection = result.output

    if stage_outputs_file:
        with open(stage_outputs_file, "a") as f:
            f.write("\n---\n## Recovery Reflection\n")
            f.write(f"**Summary**: {reflection.summary}\n")
            if reflection.investigation_observations:
                f.write("**Grounded Observations**:\n")
                f.writelines(f"- {observation}\n" for observation in reflection.investigation_observations)
            for failure in reflection.stage_failures:
                f.write(f"### {failure.stage}\n")
                f.write(f"**Description**: {failure.description}\n")
                f.write(f"**Evidence**: {failure.evidence}\n")
                f.write(f"**Lesson**: {failure.lesson}\n")

    return reflection


async def _run_recovery_mitigation(
    model: Model,
    app_info: dict[str, Any],
    shared_file: SharedFile,
    original_answer: str,
    benchmark_block: str,
    renderer: PromptRenderer,
    usage_collector: UsageCollector,
    trajectory_path: Path | None = None,
    original_justification: str = "",
    diagnosis_answer: str = "",
    stage_outputs_file: Path | None = None,
) -> SRESubmission | None:
    """Run a recovery mitigation agent to investigate and apply the correct fix.

    Called when ``include_benchmark_results`` is enabled and the benchmark
    rejected the agent's mitigation.  The recovery agent receives the
    benchmark's ground-truth reasoning, investigates the cluster, applies the
    correct fix, verifies it, and reflects on why the original mitigation was
    wrong — producing validated, evidence-backed KB entries.
    """
    reasoning = _extract_benchmark_reasoning(benchmark_block, stage="mitigation")
    if not reasoning:
        logger.warning("Recovery mitigation: no benchmark reasoning found, skipping.")
        return None

    logger.info("=" * 60)
    logger.info("RECOVERY MITIGATION: reflecting on failed mitigation attempt")
    logger.info("=" * 60)

    if stage_outputs_file:
        with open(stage_outputs_file, "a") as f:
            f.write("\n---\n## Recovery Mitigation Investigation\n")

    sre_state = SharedState()
    sre_deps = SREDeps(
        namespace=app_info.get("namespace", "default"),
        shared_file=shared_file,
        iteration=0,  # recovery — not a regular iteration
        stage="mitigation",
        model_id=model,
        renderer=renderer,
        state=sre_state,
        stage_outputs_file=stage_outputs_file,
        usage_collector=usage_collector,
    )

    system_prompt = renderer.render("recovery_mitigation_system")
    user_prompt = renderer.render(
        "recovery_mitigation_user",
        benchmark_reasoning=reasoning,
        original_answer=original_answer,
        original_justification=original_justification,
        diagnosis_answer=diagnosis_answer,
        app_name=app_info.get("app_name", "unknown"),
        namespace=app_info.get("namespace", "default"),
        descriptions=app_info.get("descriptions", ""),
    )
    logger.info(f"[recovery-mitigation] SYSTEM PROMPT:\n{system_prompt}")
    logger.info(f"[recovery-mitigation] USER PROMPT:\n{user_prompt}")

    agent = CrucibleSREAgent(model, sre_deps, trajectory_path=trajectory_path, system_prompt_override=system_prompt)
    try:
        await agent.arun(
            user_prompt,
            run_ctx={"stage": "mitigation", "iteration": 0, "role": "recovery"},
        )
    except Exception as e:
        logger.warning(f"Recovery mitigation agent failed: {e}")
        return None

    if not sre_state.submitted:
        logger.warning("Recovery mitigation agent did not submit an answer.")
        return None

    submission = SRESubmission(
        answer=sre_state.answer or "",
        justification=sre_state.answer_justification or "",
        reflection=sre_state.answer_reflection or "",
    )

    # Append recovery result to shared file for KB consumption
    entry = (
        f"\n### Recovery Mitigation\n"
        f"**Mitigation**: {submission.answer}\n"
        f"**Justification**: {submission.justification}\n"
    )
    if submission.reflection:
        entry += f"**Agent Reflection**: {submission.reflection}\n"
    try:
        shared_file.append(entry)
    except Exception as e:
        logger.warning(f"Error writing recovery mitigation to shared file: {e}")

    # Append recovery result to stage outputs file
    if stage_outputs_file:
        with open(stage_outputs_file, "a") as f:
            f.write(f"**Mitigation**: {submission.answer}\n")
            f.write(f"**Justification**: {submission.justification}\n")
            if submission.reflection:
                f.write(f"**Agent Reflection**: {submission.reflection}\n")

    logger.info(f"Recovery mitigation complete: {submission.answer}")
    return submission


def _append_stage_outcome(result: StageLoopResult, stage_label: str) -> None:
    """Append the agent's answer and benchmark result to the stage outputs file.

    This gives the reflector a single file with the full picture: intermediate
    tool outputs, the agent's conclusion, and the benchmark verdict.
    """
    sof = result.stage_outputs_file
    if not sof:
        return
    parts: list[str] = [f"\n---\n## {stage_label} Outcome\n"]
    if result.agent_answer:
        parts.append(f"**Agent Answer**: {result.agent_answer}\n")
    if result.agent_justification:
        parts.append(f"**Justification**: {result.agent_justification}\n")
    if result.agent_causal_chain:
        parts.append(f"**Causal Chain**: {result.agent_causal_chain}\n")
    if result.benchmark_block:
        parts.append(f"\n{result.benchmark_block.strip()}\n")
    with open(sof, "a") as f:
        f.write("".join(parts))


async def run(
    model: str | Model,
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
) -> dict[str, Any]:
    """Main orchestrator: runs diagnosis (and optionally mitigation) with judge-agent loop."""
    resolved_model: Model = model if isinstance(model, Model) else infer_model(model)
    if crucible_config is None:
        crucible_config = CrucibleConfig()
    max_diag_iters = crucible_config.max_diagnosis_iterations
    max_mit_iters = crucible_config.max_mitigation_iterations
    wait_stage_timeout = crucible_config.wait_stage_timeout

    # Two collectors per problem: primary covers diagnosis + mitigation,
    # recovery covers the post-failure recovery agents. Recovery is fenced
    # so we can subtract its cost from the primary playbook ablation cleanly.
    primary_collector = UsageCollector()
    recovery_collector = UsageCollector()

    diagnosis_sf = SharedFile(diagnosis_shared_file.resolve())
    diagnosis_sf.init(
        "# SRE Judged Session State\n"
        "## Session\n"
        f"- App: {app_info.get('app_name', 'unknown')} "
        f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
        "## Diagnosis\n"
    )
    logger.info(f"Initialized diagnosis shared file: {diagnosis_sf}")
    injected_kb = _resolve_injected_kb(injected_kb)

    diag_result = await _run_stage_loop(
        resolved_model,
        app_info,
        "diagnosis",
        max_diag_iters,
        diagnosis_sf,
        submit_mcp_url,
        renderer=renderer,
        usage_collector=primary_collector,
        injected_kb=injected_kb,
        trajectory_path=trajectory_path,
        crucible_config=crucible_config,
    )
    _append_stage_outcome(diag_result, "Diagnosis")
    # Recovery diagnosis: produce a validated causal chain when the benchmark
    # rejected the agent's diagnosis and we want causal chains for KB.
    if (
        crucible_config.include_benchmark_results
        and diag_result.benchmark_block
        and "success: False" in diag_result.benchmark_block
    ):
        recovery = await _run_recovery_diagnosis(
            resolved_model,
            app_info,
            diagnosis_sf,
            diag_result.agent_answer,
            diag_result.benchmark_block,
            renderer=renderer,
            usage_collector=recovery_collector,
            trajectory_path=trajectory_path,
            original_justification=diag_result.agent_justification,
            original_causal_chain=diag_result.agent_causal_chain,
            stage_outputs_file=diag_result.stage_outputs_file,
        )
        if recovery:
            original_answer = diag_result.agent_answer
            original_justification = diag_result.agent_justification
            original_causal_chain = diag_result.agent_causal_chain
            diag_result.agent_answer = recovery.answer
            diag_result.agent_justification = recovery.justification
            diag_result.agent_causal_chain = recovery.causal_chain
            diag_result.agent_reflection = recovery.reflection
            if crucible_config.recovery_phase2_enabled:
                diag_result.recovery_reflection = await _run_recovery_reflection_phase(
                    resolved_model,
                    app_info,
                    renderer=renderer,
                    usage_collector=recovery_collector,
                    original_answer=original_answer,
                    original_justification=original_justification,
                    original_causal_chain=original_causal_chain,
                    stage_outputs_file=diag_result.stage_outputs_file,
                    phase1_messages=recovery.message_history,
                )

    if "mitigation" not in planned_stages:
        logger.info("Diagnosis-only problem — orchestrator complete.")
        result = _build_usage_metrics(primary_collector, recovery_collector)
        sof = diag_result.stage_outputs_file
        result["stage_outputs_file"] = str(sof) if sof else None
        result["recovery_reflection"] = (
            diag_result.recovery_reflection.model_dump() if diag_result.recovery_reflection else None
        )
        result["diagnosis_succeeded"] = "success: True" in (diag_result.benchmark_block or "")
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
    await _wait_for_mitigation_stage(api_base, timeout=wait_stage_timeout)

    # Playbook shortcut: if the diagnosis matched a KB class with a slug and
    # the benchmark confirmed the diagnosis, try the corresponding mitigation
    # playbook directly before running the full SRE agent loop.
    mit_result: StageLoopResult | None = None
    if (
        crucible_config.enable_playbook_shortcut
        and diag_result.benchmark_block
        and "success: True" in diag_result.benchmark_block
        and diag_result.confirmed_slugs
        and injected_kb
        and injected_kb.mitigation_playbooks_dir
    ):
        idx = _extract_matched_candidate_index(diag_result.benchmark_block)
        if idx is not None and 0 <= idx < len(diag_result.confirmed_slugs):
            slug = diag_result.confirmed_slugs[idx]
            if slug:
                logger.info(
                    "[playbook-shortcut] Attempting mitigation playbook for slug=%r (matched_candidate_index=%d)",
                    slug,
                    idx,
                )
                mit_result = await _try_playbook_shortcut(
                    model=resolved_model,
                    namespace=app_info.get("namespace", "default"),
                    slug=slug,
                    mitigation_playbooks_dir=injected_kb.mitigation_playbooks_dir.resolve(),
                    shared_file=mitigation_sf,
                    submit_mcp_url=submit_mcp_url,
                    renderer=renderer,
                    usage_collector=primary_collector,
                    trajectory_path=trajectory_path,
                )

    if mit_result is None:
        mit_result = await _run_stage_loop(
            resolved_model,
            app_info,
            "mitigation",
            max_mit_iters,
            mitigation_sf,
            submit_mcp_url,
            renderer=renderer,
            usage_collector=primary_collector,
            injected_kb=injected_kb,
            trajectory_path=trajectory_path,
            crucible_config=crucible_config,
        )
    _append_stage_outcome(mit_result, "Mitigation")
    # Recovery mitigation: reflect on why mitigation failed when benchmark
    # rejected the agent's fix and we want lessons for KB.
    if (
        crucible_config.include_benchmark_results
        and mit_result.benchmark_block
        and "success: False" in mit_result.benchmark_block
    ):
        recovery = await _run_recovery_mitigation(
            resolved_model,
            app_info,
            mitigation_sf,
            mit_result.agent_answer,
            mit_result.benchmark_block,
            renderer=renderer,
            usage_collector=recovery_collector,
            trajectory_path=trajectory_path,
            original_justification=mit_result.agent_justification,
            diagnosis_answer=diag_result.agent_answer,
            stage_outputs_file=mit_result.stage_outputs_file,
        )
        if recovery:
            mit_result.agent_answer = recovery.answer
            mit_result.agent_justification = recovery.justification
            mit_result.agent_reflection = recovery.reflection

    logger.info("=" * 60)
    logger.info("CRUCIBLE: Orchestrator complete.")
    logger.info("=" * 60)
    result = _build_usage_metrics(primary_collector, recovery_collector)
    # Prefer diagnosis stage outputs for the reflector (it has the full pipeline).
    sof = diag_result.stage_outputs_file or mit_result.stage_outputs_file
    result["stage_outputs_file"] = str(sof) if sof else None
    result["recovery_reflection"] = (
        diag_result.recovery_reflection.model_dump() if diag_result.recovery_reflection else None
    )
    result["diagnosis_succeeded"] = "success: True" in (diag_result.benchmark_block or "")
    result["mitigation_succeeded"] = "success: True" in (mit_result.benchmark_block or "")
    return result
