"""Crucible agent driver — entry point for the judge-agent benchmark client."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests

from sregym_agents.crucible import orchestrator
from sregym_agents.crucible.summary import SUMMARY_FILENAME, CrucibleLTSummarizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_READY_STAGES = {"diagnosis", "mitigation"}


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
    resp = requests.get(f"{api_base}/get_app", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _get_problem_id(api_base: str) -> str:
    resp = requests.get(f"{api_base}/get_problem", timeout=10)
    resp.raise_for_status()
    return resp.json()["problem_id"]


def _get_planned_stages(api_base: str) -> list[str]:
    resp = requests.get(f"{api_base}/stages", timeout=10)
    resp.raise_for_status()
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
        "--summary-dir",
        type=str,
        default=None,
        help="Directory for long_term_summary.txt (enables long-term summary mode when set)",
    )
    parser.add_argument(
        "--summary-model",
        type=str,
        default=None,
        help="Model ID to use for long-term summarization (defaults to MODEL_ID env var)",
    )
    parser.add_argument(
        "--no-inject-summary",
        action="store_true",
        default=False,
        help="Update the long-term summary after each run but do not inject it before the run",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logger.info("Crucible driver starting...")

    api_base = _get_api_base()
    mcp_port = os.getenv("MCP_SERVER_PORT", "9954")
    submit_mcp_url = f"http://localhost:{mcp_port}/submit/sse"

    logger.info(f"model={args.model} api={api_base} mcp={submit_mcp_url}")

    _wait_for_stage(api_base, timeout=300)

    app_info = _get_app_info(api_base)
    problem_id = _get_problem_id(api_base)
    planned_stages = _get_planned_stages(api_base)

    exp_env = os.getenv("SREGYM_EXP_ENV", ".")
    shared_file = Path(exp_env) / "judged_session_state.md"
    _run_uid = uuid.uuid4().hex[:8]
    # Write trajectory to logs_dir (bench/sregym/logs/…) when available, matching
    # the convention used by other sregym agents (claudecode, gemini_cli, codex).
    # Fall back to exp_env for local/standalone runs.
    if args.logs_dir:
        logs_dir = Path(args.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        trajectory_path = logs_dir / f"trajectory_{problem_id}_{_run_uid}.jsonl"
    else:
        trajectory_path = Path(exp_env) / f"trajectory_{problem_id}_{_run_uid}.jsonl"

    lt_summarizer: CrucibleLTSummarizer | None = None
    lt_summary_file: Path | None = None

    if args.summary_dir:
        model_id = args.summary_model or os.environ.get("MODEL_ID", args.model)
        lt_summarizer = CrucibleLTSummarizer(
            shared_file=shared_file,
            summary_dir=Path(args.summary_dir),
            model_id=model_id,
        )
        if not args.no_inject_summary:
            dest = Path(exp_env) / SUMMARY_FILENAME
            try:
                shutil.copy2(lt_summarizer.summary_path, dest)
                lt_summary_file = dest
                logger.info(f"Long-term summary: copied prior knowledge to {dest}")
            except FileNotFoundError:
                logger.info("Long-term summary: no prior summary found; starting fresh.")

    logger.info(f"Problem: {problem_id} | Stages: {planned_stages}")

    usage_metrics = orchestrator.run(
        model=args.model,
        app_info=app_info,
        problem_id=problem_id,
        shared_file=shared_file,
        planned_stages=planned_stages,
        submit_mcp_url=submit_mcp_url,
        lt_summary_file=lt_summary_file,
        trajectory_path=trajectory_path,
    )

    if args.logs_dir:
        _save_results(logs_dir, problem_id, usage_metrics)
        logger.info(f"Usage metrics: {usage_metrics}")

    env_log_file = os.environ.get("SREGYM_LOG_FILE")
    if env_log_file and shared_file.exists():
        dest = Path(env_log_file).with_name(f"{Path(env_log_file).stem}_{problem_id}.md")
        shutil.copy2(shared_file, dest)
        logger.info(f"Saved session markdown to {dest}")

    if lt_summarizer is not None:
        logger.info("Long-term summary: updating from completed session.")
        lt_summarizer.run()

    logger.info("Crucible driver complete.")


if __name__ == "__main__":
    main()
