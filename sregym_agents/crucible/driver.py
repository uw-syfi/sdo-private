"""Crucible agent driver — entry point for the judge-agent benchmark client."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from filelock import FileLock

from libs.agent_mw import request_with_retry
from sregym_agents.crucible import orchestrator
from sregym_agents.crucible._prompts import configure as configure_prompts
from sregym_agents.crucible.knowledge_base import KnowledgeBase, create_knowledge_base
from sregym_agents.crucible.orchestrator import CrucibleFlags

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_READY_STAGES = {"diagnosis", "mitigation"}


def _pid_is_alive(pid: int) -> bool:
    """Check whether a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _ensure_kb_worker(kb_dir: Path, model_id: str) -> None:
    """Spawn a detached KB worker if one is not already running.

    Uses a file lock to prevent race conditions when multiple driver
    processes start simultaneously.
    """
    lock_path = kb_dir / "kb_worker.lock"
    pid_path = kb_dir / "kb_worker.pid"

    with FileLock(lock_path, timeout=10):
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
            except (ValueError, OSError):
                pid = -1
            if _pid_is_alive(pid):
                logger.info("KB worker already running (pid=%d)", pid)
                return
            logger.info("Stale KB worker PID file (pid=%d), respawning.", pid)
            pid_path.unlink(missing_ok=True)

        log_path = kb_dir / "kb_worker.log"
        log_file = open(log_path, "a")  # noqa: SIM115
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sregym_agents.crucible.kb_worker",
                "--kb-dir",
                str(kb_dir),
            ],
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
        )
        pid_path.write_text(str(proc.pid))
        logger.info("Spawned KB worker (pid=%d), log at %s", proc.pid, log_path)


def _load_crucible_config() -> tuple[dict, str]:
    """Load crucible agent config and return (config_dict, source).

    Reads from SREGYM_EXPERIMENT_AGENT_CONFIG env var (set by the centralized
    launcher via run_sregym.sh). Returns hardcoded defaults when the env var
    is absent.
    """
    env_cfg = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG")
    if env_cfg:
        logger.info("Crucible config loaded from SREGYM_EXPERIMENT_AGENT_CONFIG env var")
        return {"agent": json.loads(env_cfg)}, "env:SREGYM_EXPERIMENT_AGENT_CONFIG"
    logger.warning(
        "SREGYM_EXPERIMENT_AGENT_CONFIG not set — using hardcoded defaults. "
        "Start experiments via scripts/run_sregym.sh to apply experiment config."
    )
    return {}, "defaults"


def _get_api_base() -> str:
    host = os.getenv("API_HOSTNAME", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def _wait_for_stage(api_base: str, timeout: int = 300) -> str:
    """Poll until conductor reaches a submission-ready stage."""
    start = time.time()
    delay = 1.0
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{api_base}/status", timeout=5)
            resp.raise_for_status()
            stage = resp.json().get("stage")
            if stage in _READY_STAGES:
                logger.info(f"Conductor ready at stage: {stage!r}")
                return stage
            logger.debug(f"Stage: {stage!r}, waiting...")
        except Exception as e:
            logger.debug(f"Status check failed: {e}")
        time.sleep(delay + random.uniform(0, delay * 0.1))
        delay = min(delay * 1.5, 30)
    raise TimeoutError(f"Conductor did not reach ready stage within {timeout}s")


def _get_app_info(api_base: str) -> dict:
    resp = request_with_retry("GET", f"{api_base}/get_app", timeout=10)
    return resp.json()


def _get_problem_id(api_base: str) -> str:
    resp = request_with_retry("GET", f"{api_base}/get_problem", timeout=10)
    return resp.json()["problem_id"]


def _get_planned_stages(api_base: str) -> list[str]:
    resp = request_with_retry("GET", f"{api_base}/stages", timeout=10)
    return resp.json().get("stages", [])


def _save_results(logs_dir: Path, problem_id: str, usage_metrics: dict) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = logs_dir / f"crucible_results_{problem_id}_{timestamp}.json"
    results = {
        "problem_id": problem_id,
        "timestamp": timestamp,
        "usage_metrics": usage_metrics,
    }
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Saved results to {results_file}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crucible judge-agent benchmark client")
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("MODEL_ID", "claude-sonnet-4-6"),
        help="Model to use (default: MODEL_ID env var or claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--logs-dir",
        type=str,
        default=None,
        help="Directory for result JSON output (e.g. token usage)",
    )
    parser.add_argument(
        "--kb-dir",
        "--summary-dir",
        type=str,
        default=None,
        dest="kb_dir",
        help="Directory for knowledge base files (enables KB mode when set)",
    )
    parser.add_argument(
        "--kb-model",
        "--summary-model",
        type=str,
        default=None,
        dest="kb_model",
        help="Model ID to use for knowledge base summarization (defaults to MODEL_ID env var)",
    )
    parser.add_argument(
        "--kb-type",
        type=str,
        default=None,
        choices=["structured", "append-only"],
        dest="kb_type",
        help="Knowledge base implementation (overrides crucible.toml; default: structured)",
    )
    parser.add_argument(
        "--no-inject-kb",
        "--no-inject-summary",
        action="store_true",
        default=False,
        dest="no_inject_kb",
        help="Update the knowledge base after each run but do not inject it before the run",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        default=False,
        help="Skip judge agent and submit SRE agent's answer directly to benchmark (overrides crucible.toml)",
    )
    parser.add_argument(
        "--prompt-version",
        type=str,
        default=None,
        dest="prompt_version",
        help="Prompt version directory name (e.g. 'v1'). Required unless set in agent config.",
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> None:
    logger.info("Crucible driver starting...")

    crucible_cfg, config_source = _load_crucible_config()
    agent_cfg = crucible_cfg.get("agent", {})
    logger.info(f"Effective agent config (source={config_source}): {agent_cfg}")
    prompt_version = args.prompt_version or agent_cfg.get("prompt_version")
    if not prompt_version:
        logger.error(
            "prompt_version is required. Set it in [agent.crucible] config "
            "or pass --prompt-version on the command line."
        )
        sys.exit(1)
    configure_prompts(prompt_version)

    enable_judge = agent_cfg.get("enable_judge", True)
    if args.no_judge:
        enable_judge = False
    flags = CrucibleFlags(
        prompt_version=prompt_version,
        enable_judge=enable_judge,
        enable_ltm_retrieval=agent_cfg.get("enable_ltm_retrieval", False),
        include_benchmark_results=agent_cfg.get("include_benchmark_results", False),
        enable_heuristic_refinement=agent_cfg.get("enable_heuristic_refinement", True),
        max_diagnosis_iterations=agent_cfg.get("max_diagnosis_iterations", 5),
        max_mitigation_iterations=agent_cfg.get("max_mitigation_iterations", 5),
        wait_stage_timeout=agent_cfg.get("wait_stage_timeout", 300),
        stage_timeout=agent_cfg.get("stage_timeout", 900),
    )

    api_base = _get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    submit_mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    exp_env = os.getenv("SREGYM_EXP_ENV")
    if exp_env:
        if not os.path.isdir(exp_env):
            logger.error(f"SREGYM_EXP_ENV={exp_env} is not a valid directory")
            sys.exit(1)
        os.chdir(exp_env)
        logger.info(f"Working directory: {os.getcwd()}")
    else:
        logger.warning("SREGYM_EXP_ENV is not set — running in cwd: %s", os.getcwd())
    logger.info(f"model={args.model} api={api_base} mcp={submit_mcp_url} flags={flags}")

    _wait_for_stage(api_base, timeout=300)

    app_info = _get_app_info(api_base)
    problem_id = _get_problem_id(api_base)
    planned_stages = _get_planned_stages(api_base)

    diagnosis_shared_file = Path("diagnosis_session_state.md")
    mitigation_shared_file = Path("mitigation_session_state.md")
    _run_uid = uuid.uuid4().hex[:8]
    # Write trajectory to logs_dir (bench/sregym/logs/…) when available, matching
    # the convention used by other sregym agents (claudecode, gemini_cli, codex).
    # Fall back to cwd (exp_env after chdir) for local/standalone runs.
    if args.logs_dir:
        logs_dir = Path(args.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        trajectory_path = logs_dir / f"trajectory_{problem_id}_{_run_uid}.jsonl"
    else:
        trajectory_path = Path(f"trajectory_{problem_id}_{_run_uid}.jsonl")

    kb: KnowledgeBase | None = None
    lt_summary_file: Path | None = None
    lessons_file: Path | None = None
    architecture_file: Path | None = None
    incidents_dir: Path | None = None
    diagnosis_heuristics_file: Path | None = None
    triage_heuristics_file: Path | None = None
    arbitration_heuristics_file: Path | None = None

    if args.kb_dir:
        model_id = args.kb_model or os.environ.get("MODEL_ID", args.model)
        seed_kb_dir_str = os.environ.get("CRUCIBLE_SEED_KB_DIR")
        seed_kb_dir = Path(seed_kb_dir_str) if seed_kb_dir_str else None
        kb_type = args.kb_type or agent_cfg.get("kb_type", "structured")
        include_benchmark_results = agent_cfg.get("include_benchmark_results", False)
        enable_heuristic_refinement = agent_cfg.get("enable_heuristic_refinement", True)
        include_incident_files = agent_cfg.get("include_incident_files", True)
        kb = create_knowledge_base(
            kb_type=kb_type,
            shared_files=[diagnosis_shared_file, mitigation_shared_file],
            kb_dir=Path(args.kb_dir),
            model_id=model_id,
            app_name=app_info.get("app_name", "unknown"),
            seed_kb_dir=seed_kb_dir,
            include_benchmark_results=include_benchmark_results,
            enable_heuristic_refinement=enable_heuristic_refinement,
            include_incident_files=include_incident_files,
        )
        if not args.no_inject_kb:
            injected = await kb.inject(Path(exp_env))
            lt_summary_file = injected.summary
            lessons_file = injected.lessons
            architecture_file = injected.architecture
            incidents_dir = injected.incidents_dir
            if agent_cfg.get("inject_heuristics", True):
                diagnosis_heuristics_file = injected.diagnosis_heuristics
                triage_heuristics_file = injected.triage_heuristics
                arbitration_heuristics_file = injected.arbitration_heuristics
            else:
                logger.info("Heuristic injection disabled by inject_heuristics=false")

    logger.info(f"Problem: {problem_id} | Stages: {planned_stages}")

    usage_metrics = await orchestrator.run(
        model=args.model,
        app_info=app_info,
        problem_id=problem_id,
        diagnosis_shared_file=diagnosis_shared_file,
        mitigation_shared_file=mitigation_shared_file,
        planned_stages=planned_stages,
        submit_mcp_url=submit_mcp_url,
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

    stage_outputs_file_str = usage_metrics.get("stage_outputs_file")
    stage_outputs_file = Path(stage_outputs_file_str) if stage_outputs_file_str else None

    if args.logs_dir:
        _save_results(logs_dir, problem_id, usage_metrics)
        logger.info(f"Usage metrics: {usage_metrics}")

    env_log_file = os.environ.get("SREGYM_LOG_FILE")
    if env_log_file:
        stem = Path(env_log_file).stem
        for sf, suffix in [(diagnosis_shared_file, "diagnosis"), (mitigation_shared_file, "mitigation")]:
            if sf.exists():
                dest = Path(env_log_file).with_name(f"{stem}_{problem_id}_{suffix}.md")
                shutil.copy2(sf, dest)
                logger.info(f"Saved {suffix} session markdown to {dest}")

    if kb is not None and args.kb_dir:
        # Copy stage_outputs_file to logs_dir so it survives exp_env cleanup
        saved_stage_outputs: str | None = None
        if stage_outputs_file and stage_outputs_file.exists() and args.logs_dir:
            dest = logs_dir / stage_outputs_file.name
            shutil.copy2(stage_outputs_file, dest)
            saved_stage_outputs = str(dest)
            logger.info(f"Saved stage outputs to {dest}")

        # Collect paths to session markdown copies already saved above
        session_files: list[str] = []
        if env_log_file:
            stem = Path(env_log_file).stem
            for suffix in ["diagnosis", "mitigation"]:
                p = Path(env_log_file).with_name(f"{stem}_{problem_id}_{suffix}.md")
                if p.exists():
                    session_files.append(str(p))

        if session_files:
            manifest = {
                "session_files": session_files,
                "stage_outputs_file": saved_stage_outputs,
                "kb_dir": args.kb_dir,
                "kb_type": args.kb_type or agent_cfg.get("kb_type", "structured"),
                "model_id": args.kb_model or os.environ.get("MODEL_ID", args.model),
                "app_name": app_info.get("app_name", "unknown"),
                "include_benchmark_results": agent_cfg.get("include_benchmark_results", False),
                "enable_heuristic_refinement": agent_cfg.get("enable_heuristic_refinement", True),
                "include_incident_files": agent_cfg.get("include_incident_files", True),
                "problem_id": problem_id,
                "prompt_version": prompt_version,
                "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            }
            pending_dir = Path(args.kb_dir) / "pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = pending_dir / f"{manifest['timestamp']}_{problem_id}.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))
            logger.info(f"KB update manifest written to {manifest_path}")

            _ensure_kb_worker(
                Path(args.kb_dir),
                manifest["model_id"],
            )
        else:
            # Standalone mode (no SREGYM_LOG_FILE) — run KB update inline
            logger.info("Knowledge base: updating inline (no sregym harness detected).")
            await kb.update(stage_outputs_file=stage_outputs_file)

    logger.info("Crucible driver complete.")


def main() -> None:
    args = _parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
