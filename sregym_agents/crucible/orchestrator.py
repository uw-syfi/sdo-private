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
from typing import TYPE_CHECKING

import httpx
import yaml

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.judge_agent import CrucibleJudgeAgent
from sregym_agents.crucible.sre_agent import CrucibleSREAgent
from sregym_agents.crucible.tools import (
    JudgeDeps,
    SharedFile,
    SharedState,
    SREDeps,
    SRESubmission,
    _submit_to_benchmark,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CrucibleFlags:
    prompt_version: str = "v2"
    enable_judge: bool = True
    enable_ltm_retrieval: bool = False
    include_benchmark_results: bool = False
    enable_heuristic_refinement: bool = True
    max_diagnosis_iterations: int = 5
    max_mitigation_iterations: int = 5
    wait_stage_timeout: int = 300
    stage_timeout: int = 900  # 15 minutes max per diagnosis/mitigation stage


@dataclasses.dataclass
class StageLoopResult:
    """Result from a single stage's agent-judge loop."""

    approved: bool
    usage_by_role: dict
    benchmark_block: str = ""
    agent_answer: str = ""
    agent_justification: str = ""
    agent_causal_chain: str = ""
    agent_reflection: str = ""
    stage_outputs_file: Path | None = None


_CONFIG_DIR = Path(__file__).parent / "configs"


def _load_agent_config() -> dict:
    with open(_CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f)


def _zero_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}


def _add_usage(a: dict, b: dict) -> dict:
    return {k: a[k] + b.get(k, 0) for k in a}


def _build_usage_result(usage_by_agent: dict) -> dict:
    total = _zero_usage()
    for agent_data in usage_by_agent.values():
        total = _add_usage(total, agent_data["total"])
    return {"by_agent": usage_by_agent, "total": total}


def _write_timeout_entry(
    shared_file: Path,
    label: str,
    timeout: int,
    elapsed: float | None = None,
    context: str = "",
) -> None:
    elapsed_str = f" ({elapsed:.0f}s)" if elapsed is not None else ""
    detail = f" {context}" if context else ""
    with shared_file.open("a") as fh:
        fh.write(f"\n### {label} — TIMED OUT\nExceeded {timeout}s limit{detail}{elapsed_str}.\n")


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
    app_info: dict,
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


async def _run_stage_loop(
    model: str,
    app_info: dict,
    stage: str,
    max_iters: int,
    shared_file: SharedFile,
    submit_mcp_url: str,
    renderer: PromptRenderer,
    lt_summary_file: Path | None = None,
    lessons_file: Path | None = None,
    architecture_file: Path | None = None,
    incidents_dir: Path | None = None,
    trajectory_path: Path | None = None,
    flags: CrucibleFlags | None = None,
    diagnosis_heuristics_file: Path | None = None,
    triage_heuristics_file: Path | None = None,
    arbitration_heuristics_file: Path | None = None,
) -> StageLoopResult:
    """Run the agent->judge loop for one stage."""
    if flags is None:
        flags = CrucibleFlags()
    stage_timeout = flags.stage_timeout
    stage_start = time.monotonic()
    logger.info("=" * 60)
    logger.info(f"CRUCIBLE: Starting {stage.upper()} stage (timeout={stage_timeout}s)")
    logger.info("=" * 60)

    # When LTM retrieval is enabled, don't inject summary into the SRE prompt —
    # the SRE agent will use the search_prior_incidents tool instead.
    # The judge always receives the full summary regardless of the flag.
    full_lt_summary_content = _read_kb_content(lt_summary_file)
    if flags.enable_ltm_retrieval:
        lt_summary_content = ""
    else:
        lt_summary_content = full_lt_summary_content
    # Lessons and architecture are always injected unconditionally.
    lessons_content = _read_kb_content(lessons_file)
    architecture_content = _read_kb_content(architecture_file)

    # v3 trained heuristics
    is_v3 = flags.prompt_version >= "v3"
    diagnosis_guidance = ""
    triage_guidance = ""
    arbitration_guidance = ""
    stage_outputs_file: Path | None = None
    if is_v3:
        stage_outputs_file = Path(f"{stage}_stage_outputs.md")
        if diagnosis_heuristics_file and diagnosis_heuristics_file.exists():
            diagnosis_guidance = diagnosis_heuristics_file.read_text().strip()
        if triage_heuristics_file and triage_heuristics_file.exists():
            triage_guidance = triage_heuristics_file.read_text().strip()
        if arbitration_heuristics_file and arbitration_heuristics_file.exists():
            arbitration_guidance = arbitration_heuristics_file.read_text().strip()

    agent_role = f"{stage}-agent"
    judge_role = f"{stage}-judge"
    usage_by_role: dict[str, dict] = {
        agent_role: {"iterations": [], "total": _zero_usage()},
        judge_role: {"iterations": [], "total": _zero_usage()},
    }

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
            lt_summary_file=lt_summary_file if flags.enable_ltm_retrieval else None,
            incidents_dir=incidents_dir if flags.enable_ltm_retrieval else None,
            ltm_model_id=model if flags.enable_ltm_retrieval else None,
            trajectory_path=trajectory_path,
            triage_guidance=triage_guidance,
            arbitration_guidance=arbitration_guidance,
            stage_outputs_file=stage_outputs_file,
        )
        if is_v3:
            guidance = diagnosis_guidance if stage == "diagnosis" else ""
            sre_system = renderer.render(f"{stage}_agent_system", diagnosis_guidance=guidance)
        else:
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
        _, sre_usage = await sre_agent.arun(sre_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "sre"})
        usage_by_role[agent_role]["iterations"].append(sre_usage)
        usage_by_role[agent_role]["total"] = _add_usage(usage_by_role[agent_role]["total"], sre_usage)

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

        if not flags.enable_judge:
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
                success, message, oracle = await _submit_to_benchmark(submit_mcp_url, answer, stage)
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
                usage_by_role=usage_by_role,
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
        _, judge_usage = await judge_agent.arun(
            judge_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "judge"}
        )
        usage_by_role[judge_role]["iterations"].append(judge_usage)
        usage_by_role[judge_role]["total"] = _add_usage(usage_by_role[judge_role]["total"], judge_usage)

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
                usage_by_role=usage_by_role,
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
        usage_by_role=usage_by_role,
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


async def _run_recovery_diagnosis(
    model: str,
    app_info: dict,
    shared_file: SharedFile,
    original_answer: str,
    benchmark_block: str,
    renderer: PromptRenderer,
    trajectory_path: Path | None = None,
    original_justification: str = "",
    original_causal_chain: str = "",
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

    sre_state = SharedState()
    sre_deps = SREDeps(
        namespace=app_info.get("namespace", "default"),
        shared_file=shared_file,
        iteration=0,  # recovery — not a regular iteration
        stage="diagnosis",
        renderer=renderer,
        state=sre_state,
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
        _, usage = await agent.arun(
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

    logger.info(f"Recovery diagnosis complete: {submission.answer}")
    return submission


async def _run_recovery_mitigation(
    model: str,
    app_info: dict,
    shared_file: SharedFile,
    original_answer: str,
    benchmark_block: str,
    renderer: PromptRenderer,
    trajectory_path: Path | None = None,
    original_justification: str = "",
    diagnosis_answer: str = "",
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

    sre_state = SharedState()
    sre_deps = SREDeps(
        namespace=app_info.get("namespace", "default"),
        shared_file=shared_file,
        iteration=0,  # recovery — not a regular iteration
        stage="mitigation",
        renderer=renderer,
        state=sre_state,
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
        _, usage = await agent.arun(
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

    logger.info(f"Recovery mitigation complete: {submission.answer}")
    return submission


async def run(
    model: str,
    app_info: dict,
    problem_id: str,
    diagnosis_shared_file: Path,
    mitigation_shared_file: Path,
    planned_stages: list[str],
    submit_mcp_url: str,
    renderer: PromptRenderer,
    lt_summary_file: Path | None = None,
    lessons_file: Path | None = None,
    architecture_file: Path | None = None,
    incidents_dir: Path | None = None,
    trajectory_path: Path | None = None,
    flags: CrucibleFlags | None = None,
    diagnosis_heuristics_file: Path | None = None,
    triage_heuristics_file: Path | None = None,
    arbitration_heuristics_file: Path | None = None,
) -> dict:
    """Main orchestrator: runs diagnosis (and optionally mitigation) with judge-agent loop."""
    if flags is None:
        flags = CrucibleFlags()
    max_diag_iters = flags.max_diagnosis_iterations
    max_mit_iters = flags.max_mitigation_iterations
    wait_stage_timeout = flags.wait_stage_timeout

    diagnosis_sf = SharedFile(diagnosis_shared_file.resolve())
    diagnosis_sf.init(
        "# SRE Judged Session State\n"
        "## Session\n"
        f"- App: {app_info.get('app_name', 'unknown')} "
        f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
        "## Diagnosis\n"
    )
    logger.info(f"Initialized diagnosis shared file: {diagnosis_sf}")
    lt_summary_file = lt_summary_file.resolve() if lt_summary_file else None
    lessons_file = lessons_file.resolve() if lessons_file else None
    architecture_file = architecture_file.resolve() if architecture_file else None
    incidents_dir = incidents_dir.resolve() if incidents_dir else None
    diagnosis_heuristics_file = diagnosis_heuristics_file.resolve() if diagnosis_heuristics_file else None
    triage_heuristics_file = triage_heuristics_file.resolve() if triage_heuristics_file else None
    arbitration_heuristics_file = arbitration_heuristics_file.resolve() if arbitration_heuristics_file else None

    diag_result = await _run_stage_loop(
        model,
        app_info,
        "diagnosis",
        max_diag_iters,
        diagnosis_sf,
        submit_mcp_url,
        renderer=renderer,
        lt_summary_file=lt_summary_file,
        lessons_file=lessons_file,
        architecture_file=architecture_file,
        incidents_dir=incidents_dir,
        trajectory_path=trajectory_path,
        flags=flags,
        diagnosis_heuristics_file=diagnosis_heuristics_file,
        triage_heuristics_file=triage_heuristics_file,
        arbitration_heuristics_file=arbitration_heuristics_file,
    )
    # Recovery diagnosis: produce a validated causal chain when the benchmark
    # rejected the agent's diagnosis and we want causal chains for KB.
    if (
        flags.include_benchmark_results
        and diag_result.benchmark_block
        and "success: False" in diag_result.benchmark_block
    ):
        recovery = await _run_recovery_diagnosis(
            model,
            app_info,
            diagnosis_sf,
            diag_result.agent_answer,
            diag_result.benchmark_block,
            renderer=renderer,
            trajectory_path=trajectory_path,
            original_justification=diag_result.agent_justification,
            original_causal_chain=diag_result.agent_causal_chain,
        )
        if recovery:
            diag_result.agent_answer = recovery.answer
            diag_result.agent_justification = recovery.justification
            diag_result.agent_causal_chain = recovery.causal_chain
            diag_result.agent_reflection = recovery.reflection

    usage_by_agent = diag_result.usage_by_role

    if "mitigation" not in planned_stages:
        logger.info("Diagnosis-only problem — orchestrator complete.")
        result = _build_usage_result(usage_by_agent)
        sof = diag_result.stage_outputs_file
        result["stage_outputs_file"] = str(sof) if sof else None
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

    mit_result = await _run_stage_loop(
        model,
        app_info,
        "mitigation",
        max_mit_iters,
        mitigation_sf,
        submit_mcp_url,
        renderer=renderer,
        lt_summary_file=lt_summary_file,
        lessons_file=lessons_file,
        architecture_file=architecture_file,
        incidents_dir=incidents_dir,
        trajectory_path=trajectory_path,
        flags=flags,
        diagnosis_heuristics_file=diagnosis_heuristics_file,
        triage_heuristics_file=triage_heuristics_file,
        arbitration_heuristics_file=arbitration_heuristics_file,
    )
    # Recovery mitigation: reflect on why mitigation failed when benchmark
    # rejected the agent's fix and we want lessons for KB.
    if (
        flags.include_benchmark_results
        and mit_result.benchmark_block
        and "success: False" in mit_result.benchmark_block
    ):
        recovery = await _run_recovery_mitigation(
            model,
            app_info,
            mitigation_sf,
            mit_result.agent_answer,
            mit_result.benchmark_block,
            renderer=renderer,
            trajectory_path=trajectory_path,
            original_justification=mit_result.agent_justification,
            diagnosis_answer=diag_result.agent_answer,
        )
        if recovery:
            mit_result.agent_answer = recovery.answer
            mit_result.agent_justification = recovery.justification
            mit_result.agent_reflection = recovery.reflection

    usage_by_agent = {**diag_result.usage_by_role, **mit_result.usage_by_role}

    logger.info("=" * 60)
    logger.info("CRUCIBLE: Orchestrator complete.")
    logger.info("=" * 60)
    result = _build_usage_result(usage_by_agent)
    # Prefer diagnosis stage outputs for the reflector (it has the full pipeline).
    sof = diag_result.stage_outputs_file or mit_result.stage_outputs_file
    result["stage_outputs_file"] = str(sof) if sof else None
    return result
