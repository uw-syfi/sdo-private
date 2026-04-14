"""Crucible agent driver — entry point for the judge-agent benchmark client."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import random
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import requests

if TYPE_CHECKING:
    from sregym_agents.crucible.agents.base import AgentDriver
    from sregym_agents.crucible.knowledge_base.structured import StructuredKnowledgeBase

from libs.agent_mw import request_with_retry
from sregym_agents.crucible import orchestrator
from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import CrucibleConfig, crucible_config_from_experiment_agent
from sregym_agents.crucible.kb_update_queue import enqueue_task, ensure_kb_worker
from sregym_agents.crucible.knowledge_base import InjectedKB, KnowledgeBase, create_knowledge_base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_READY_STAGES = {"diagnosis", "mitigation"}


def create_driver(
    model: str,
    config: CrucibleConfig,
    trajectory_path: Path | None = None,
) -> AgentDriver:
    """Create an AgentDriver based on the configured backend.

    Returns a ``PydanticAIDriver`` for ``backend="pydantic-ai"`` (default)
    or an ``AgentCLIDriver`` for ``backend="agent-cli"``.
    """
    print(f"[crucible] Driver backend: {config.backend}")
    if config.backend == "agent-cli":
        from sregym_agents.crucible.agents.drivers.agent_cli_driver import AgentCLIDriver
        from sregym_agents.crucible.sandbox import build_crucible_sandbox

        exp_cwd = os.path.abspath(os.getcwd())
        sandbox_cfg = build_crucible_sandbox(exp_cwd)
        print(
            f"[crucible] Using AgentCLIDriver (provider={config.agent_cli_provider}, "
            f"model={model}, sandbox=workspace-only cwd={exp_cwd})"
        )
        return AgentCLIDriver(
            provider=config.agent_cli_provider,
            model=model,
            cwd=exp_cwd,
            sandbox=sandbox_cfg,
        )

    from sregym_agents.crucible.agents import PydanticAIDriver
    from sregym_agents.crucible.tools import LTMShortCircuit

    print(f"[crucible] Using PydanticAIDriver (model={model})")
    return PydanticAIDriver(
        model,
        trajectory_path=trajectory_path,
        interrupt_exceptions=(LTMShortCircuit,),
    )


def _load_crucible_config() -> tuple[dict[str, Any], str]:
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


def _get_app_info(api_base: str) -> dict[str, Any]:
    resp = request_with_retry("GET", f"{api_base}/get_app", timeout=10)
    return resp.json()


def _get_problem_id(api_base: str) -> str:
    resp = request_with_retry("GET", f"{api_base}/get_problem", timeout=10)
    return resp.json()["problem_id"]


def _get_planned_stages(api_base: str) -> list[str]:
    resp = request_with_retry("GET", f"{api_base}/stages", timeout=10)
    return resp.json().get("stages", [])


def _save_results(logs_dir: Path, problem_id: str, usage_metrics: dict[str, Any]) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = logs_dir / f"crucible_results_{problem_id}_{timestamp}.json"
    results: dict[str, Any] = {
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
        choices=["structured"],
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
    agent_cfg: dict[str, Any] = crucible_cfg.get("agent", {})
    logger.info(f"Effective agent config (source={config_source}): {agent_cfg}")
    try:
        crucible_config = crucible_config_from_experiment_agent(agent_cfg, cli_args=args)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
    renderer = PromptRenderer(crucible_config.prompt_version)

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
    logger.info(f"model={args.model} api={api_base} mcp={submit_mcp_url} crucible_config={crucible_config}")
    print("\n" + "=" * 60)
    print("[crucible] CONFIGURATION")
    print("=" * 60)
    for field in dataclasses.fields(crucible_config):
        print(f"  {field.name}: {getattr(crucible_config, field.name)!r}")
    print("=" * 60 + "\n")
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
    logs_dir: Path | None = None
    if args.logs_dir:
        logs_dir = Path(args.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        trajectory_path = logs_dir / f"trajectory_{problem_id}_{_run_uid}.jsonl"
    else:
        trajectory_path = Path(f"trajectory_{problem_id}_{_run_uid}.jsonl")

    kb: KnowledgeBase | None = None
    injected_kb: InjectedKB | None = None
    kb_type = args.kb_type or agent_cfg.get("kb_type", "structured")

    if args.kb_dir:
        model_id: str = args.kb_model or os.environ.get("MODEL_ID", args.model) or args.model
        from sregym_agents.crucible.agents import PydanticAIDriver as _KBDriver

        kb_driver = _KBDriver(model_id)
        kb = create_knowledge_base(
            kb_type=kb_type,
            kb_dir=Path(args.kb_dir),
            app_name=app_info.get("app_name", "unknown"),
            config=crucible_config,
            renderer=renderer,
            driver=kb_driver,
        )
        if not args.no_inject_kb:
            injected = await kb.inject(Path(exp_env or "."))
            if agent_cfg.get("inject_priors", agent_cfg.get("inject_heuristics", True)):
                injected_kb = injected
            else:
                logger.info("Prior injection disabled by inject_priors=false")
                injected_kb = InjectedKB(
                    architecture=injected.architecture,
                    kb_view_dir=injected.kb_view_dir,
                )

    logger.info(f"Problem: {problem_id} | Stages: {planned_stages}")

    driver = create_driver(args.model, crucible_config, trajectory_path=trajectory_path)

    usage_metrics = await orchestrator.run(
        model=args.model,
        app_info=app_info,
        problem_id=problem_id,
        diagnosis_shared_file=diagnosis_shared_file,
        mitigation_shared_file=mitigation_shared_file,
        planned_stages=planned_stages,
        submit_mcp_url=submit_mcp_url,
        renderer=renderer,
        injected_kb=injected_kb,
        trajectory_path=trajectory_path,
        crucible_config=crucible_config,
        driver=driver,
    )

    stage_outputs_file_str = usage_metrics.get("stage_outputs_file")
    stage_outputs_file = Path(stage_outputs_file_str) if stage_outputs_file_str else None
    diagnosis_succeeded = bool(usage_metrics.get("diagnosis_succeeded", False))
    mitigation_succeeded = bool(usage_metrics.get("mitigation_succeeded", False))
    original_run_md = str(usage_metrics.get("original_run_md", ""))
    grounded_run_md = str(usage_metrics.get("grounded_run_md", ""))

    if args.logs_dir:
        assert logs_dir is not None
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_run_path = None
        grounded_run_path = None
        if kb_type == "structured":
            structured_kb = cast("StructuredKnowledgeBase", kb)
            original_run_path, grounded_run_path = structured_kb.write_incident_records(
                timestamp=timestamp,
                original_run_md=original_run_md,
                grounded_run_md=grounded_run_md,
            )

        # Copy stage_outputs_file to logs_dir so it survives exp_env cleanup
        saved_stage_outputs: str | None = None
        if stage_outputs_file and stage_outputs_file.exists() and args.logs_dir:
            assert logs_dir is not None
            dest = logs_dir / stage_outputs_file.name
            shutil.copy2(stage_outputs_file, dest)
            saved_stage_outputs = str(dest)
            logger.info(f"Saved stage outputs to {dest}")

        if original_run_path and grounded_run_path:
            task_payload: dict[str, Any] = {
                "original_run_file": str(original_run_path),
                "grounded_run_file": str(grounded_run_path),
                "stage_outputs_file": saved_stage_outputs,
                "kb_dir": args.kb_dir,
                "kb_type": args.kb_type or agent_cfg.get("kb_type", "structured"),
                "model_id": args.kb_model or os.environ.get("MODEL_ID", args.model),
                "app_name": app_info.get("app_name", "unknown"),
                **crucible_config.to_kb_task_fields(),
                "problem_id": problem_id,
                "diagnosis_succeeded": diagnosis_succeeded,
                "mitigation_succeeded": mitigation_succeeded,
                "timestamp": timestamp,
            }
            task_path = enqueue_task(Path(args.kb_dir), task_payload, problem_id=problem_id)
            logger.info(f"KB update task written to {task_path}")

            ensure_kb_worker(Path(args.kb_dir))
        else:
            logger.info("Knowledge base: no incident records produced; skipping async review enqueue.")

    logger.info("Crucible driver complete.")


def main() -> None:
    args = _parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
