"""Sync orchestrator for the Crucible dual-agent judge loop."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
import yaml

from sregym_agents.crucible._prompts import _render
from sregym_agents.crucible.judge_agent import CrucibleJudgeAgent
from sregym_agents.crucible.sre_agent import CrucibleSREAgent
from sregym_agents.crucible.tools import JudgeDeps, SharedFile, SharedState, SREDeps

logger = logging.getLogger(__name__)

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


def _wait_for_mitigation_stage(api_base: str, timeout: int = 300) -> None:
    """Poll until conductor reaches mitigation stage."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{api_base}/status", timeout=5)
            resp.raise_for_status()
            stage = resp.json().get("stage")
            if stage == "mitigation":
                return
            logger.debug(f"Stage: {stage!r}, waiting for mitigation...")
        except Exception as e:
            logger.debug(f"Status check failed: {e}")
        time.sleep(1)
    logger.warning(f"Timed out waiting for mitigation stage after {timeout}s — proceeding anyway.")


def _run_stage_loop(
    model: str,
    app_info: dict,
    stage: str,
    max_iters: int,
    shared_file: SharedFile,
    submit_mcp_url: str,
    lt_summary_file: Path | None = None,
    trajectory_path: Path | None = None,
) -> tuple[bool, dict]:
    """Run the agent→judge loop for one stage. Returns (approved, usage_by_role)."""
    logger.info("=" * 60)
    logger.info(f"CRUCIBLE: Starting {stage.upper()} stage")
    logger.info("=" * 60)

    agent_role = f"{stage}-agent"
    judge_role = f"{stage}-judge"
    usage_by_role: dict[str, dict] = {
        agent_role: {"iterations": [], "total": _zero_usage()},
        judge_role: {"iterations": [], "total": _zero_usage()},
    }

    for iteration in range(1, max_iters + 1):
        logger.info(f"--- {stage.capitalize()} iteration {iteration}/{max_iters} ---")

        # SRE agent
        shared_content = shared_file.read()
        sre_state = SharedState()
        sre_deps = SREDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            state=sre_state,
        )
        sre_prompt = _render(
            f"{stage}_agent_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
            lt_summary_file=str(lt_summary_file) if lt_summary_file else None,
        )
        sre_agent = CrucibleSREAgent(model, sre_deps, trajectory_path=trajectory_path)
        _, sre_usage = sre_agent.run(sre_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "sre"})
        usage_by_role[agent_role]["iterations"].append(sre_usage)
        usage_by_role[agent_role]["total"] = _add_usage(usage_by_role[agent_role]["total"], sre_usage)

        # Judge agent
        shared_content = shared_file.read()
        judge_state = SharedState()
        judge_deps = JudgeDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            submit_mcp_url=submit_mcp_url,
            state=judge_state,
        )
        judge_prompt = _render(
            f"{stage}_judge_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
        )
        judge_agent = CrucibleJudgeAgent(model, judge_deps, trajectory_path=trajectory_path)
        _, judge_usage = judge_agent.run(
            judge_prompt, run_ctx={"stage": stage, "iteration": iteration, "role": "judge"}
        )
        usage_by_role[judge_role]["iterations"].append(judge_usage)
        usage_by_role[judge_role]["total"] = _add_usage(usage_by_role[judge_role]["total"], judge_usage)

        verdict = judge_state.verdict
        logger.info(f"{stage.capitalize()} iteration {iteration} verdict: {verdict!r}")

        if verdict == "APPROVED":
            logger.info(f"Judge APPROVED {stage}.")
            return True, usage_by_role

        logger.info(f"Judge REJECTED {stage} (iteration {iteration}). Looping...")

    logger.warning(f"Max {stage} iterations reached without APPROVED verdict.")
    return False, usage_by_role


def run(
    model: str,
    app_info: dict,
    problem_id: str,
    shared_file: Path,
    planned_stages: list[str],
    submit_mcp_url: str,
    lt_summary_file: Path | None = None,
    trajectory_path: Path | None = None,
) -> dict:
    """Main orchestrator: runs diagnosis (and optionally mitigation) with judge-agent loop."""
    agent_cfg = _load_agent_config()
    max_diag_iters = agent_cfg.get("max_diagnosis_iterations", 3)
    max_mit_iters = agent_cfg.get("max_mitigation_iterations", 3)
    wait_stage_timeout = agent_cfg.get("wait_stage_timeout", 300)

    sf = SharedFile(shared_file.resolve())
    sf.init(
        "# SRE Judged Session State\n"
        "## Session\n"
        f"- App: {app_info.get('app_name', 'unknown')} "
        f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
        "## Diagnosis\n"
    )
    logger.info(f"Initialized shared session file: {sf}")
    lt_summary_file = lt_summary_file.resolve() if lt_summary_file else None

    _, diag_usage = _run_stage_loop(
        model,
        app_info,
        "diagnosis",
        max_diag_iters,
        sf,
        submit_mcp_url,
        lt_summary_file=lt_summary_file,
        trajectory_path=trajectory_path,
    )
    usage_by_agent = diag_usage

    if "mitigation" not in planned_stages:
        logger.info("Diagnosis-only problem — orchestrator complete.")
        return _build_usage_result(usage_by_agent)

    sf.append("\n## Mitigation\n")

    api_base = f"http://{os.getenv('API_HOSTNAME', 'localhost')}:{os.getenv('API_PORT', '8000')}"
    logger.info("Waiting for benchmark to reach mitigation stage...")
    _wait_for_mitigation_stage(api_base, timeout=wait_stage_timeout)

    _, mit_usage = _run_stage_loop(
        model,
        app_info,
        "mitigation",
        max_mit_iters,
        sf,
        submit_mcp_url,
        lt_summary_file=lt_summary_file,
        trajectory_path=trajectory_path,
    )
    usage_by_agent = {**diag_usage, **mit_usage}

    logger.info("=" * 60)
    logger.info("CRUCIBLE: Orchestrator complete.")
    logger.info("=" * 60)
    return _build_usage_result(usage_by_agent)
