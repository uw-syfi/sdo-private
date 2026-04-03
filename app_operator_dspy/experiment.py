"""Experiment runner for baseline evaluation of the DSPy operator.

Runs the operator against a set of apps, collects structured metrics,
and saves results as JSON for reproducibility and presentation.

Usage:
    uv run -m app_operator_dspy experiment apps/pitstop apps/sockshop ...
    uv run -m app_operator_dspy experiment apps/deathstarbench/* --max-attempts 3
"""

import datetime
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import traceback

import dspy

from app_operator_dspy.lm_config import DEFAULT_MODEL, get_lm_kwargs
from app_operator_dspy.logger import get_logger, setup_logger
from app_operator_dspy.operator import DSPyOperator, configure_lm
from app_operator_dspy.token_tracker import clear_history, get_token_usage
from app_operator_dspy.tools.context import set_task_repo
from app_operator_dspy.tools.repo import init_submodules

log = get_logger("experiment")

DOCKER_CLEANUP_TIMEOUT = 60


def run_experiment(args) -> str:
    """Run the operator on each app and collect metrics.

    Returns the path to the results directory.
    """
    setup_logger()

    # Resolve and validate app paths
    app_paths = []
    for p in args.app_paths:
        resolved = os.path.abspath(p)
        if not os.path.isdir(resolved):
            log.warning("Skipping {} (not a directory)", p)
            continue
        app_paths.append(resolved)

    if not app_paths:
        log.error("No valid app directories provided")
        return ""

    # Create results directory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(
        args.results_dir,
        f"baseline_{timestamp}",
    )
    os.makedirs(results_dir, exist_ok=True)

    # Configure LM
    model = args.model or DEFAULT_MODEL
    lm_kwargs = get_lm_kwargs(model)
    lm = configure_lm(model, **lm_kwargs)
    log.info("Model: {}", model)
    log.info("Results: {}", results_dir)
    log.info("Apps: {}", len(app_paths))

    # Save experiment config
    config = {
        "model": model,
        "max_attempts": args.max_attempts,
        "monitor_checks": args.monitor_checks,
        "deploy_timeout": args.deploy_timeout,
        "health_check_timeout": args.health_check_timeout,
        "apps": [os.path.basename(p) for p in app_paths],
        "timestamp": timestamp,
        "platform": {
            "system": platform.system(),
            "node": platform.node(),
            "python": platform.python_version(),
        },
    }
    _write_json(os.path.join(results_dir, "config.json"), config)

    # Run each app
    app_results = []
    for i, app_path in enumerate(app_paths, 1):
        app_name = os.path.basename(app_path)
        log.info("")
        log.info("=" * 60)
        log.info("[{}/{}] {}", i, len(app_paths), app_name)
        log.info("=" * 60)

        result = _run_single_app(
            lm=lm,
            app_path=app_path,
            max_attempts=args.max_attempts,
            monitor_checks=args.monitor_checks,
            deploy_timeout=args.deploy_timeout,
            health_check_timeout=args.health_check_timeout,
        )
        app_results.append(result)

        # Save per-app result immediately (so partial runs are preserved)
        _write_json(
            os.path.join(results_dir, f"{app_name}.json"),
            result,
        )
        _log_result(result)

    # Generate summary
    summary = _build_summary(config, app_results)
    _write_json(os.path.join(results_dir, "summary.json"), summary)

    # Print summary table
    _print_summary_table(summary)

    log.info("")
    log.info("Results saved to: {}", results_dir)
    return results_dir


def _run_single_app(
    lm: dspy.LM,
    app_path: str,
    max_attempts: int,
    monitor_checks: int,
    deploy_timeout: int,
    health_check_timeout: int,
) -> dict:
    """Run the operator on a single app and return structured metrics."""
    app_name = os.path.basename(app_path)

    # Clean docker state before run
    _docker_cleanup()

    # Copy to temp dir, initializing git submodules if needed
    work_dir = tempfile.mkdtemp(prefix="sds_exp_")
    repo_path = os.path.join(work_dir, app_name)
    shutil.copytree(app_path, repo_path)
    init_submodules(app_path, repo_path)

    # Clear LM history for clean token tracking
    clear_history(lm)

    operator = DSPyOperator()
    set_task_repo(repo_path)

    start = time.time()
    try:
        result = operator(
            repo_path=repo_path,
            max_deploy_attempts=max_attempts,
            monitor_checks=monitor_checks,
            deploy_timeout=deploy_timeout,
            health_check_timeout=health_check_timeout,
        )
        elapsed = time.time() - start
        tokens = get_token_usage(lm)

        return {
            "app": app_name,
            "success": result.success,
            "phase": result.phase,
            "attempts": result.attempts,
            "statuses": getattr(result, "statuses", []),
            "error": getattr(result, "error", None),
            "time_seconds": round(elapsed, 1),
            "tokens": tokens,
        }
    except Exception as e:
        elapsed = time.time() - start
        tokens = get_token_usage(lm)
        return {
            "app": app_name,
            "success": False,
            "phase": "exception",
            "attempts": 0,
            "statuses": [],
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
            "time_seconds": round(elapsed, 1),
            "tokens": tokens,
        }
    finally:
        # Clean docker state after run
        _docker_cleanup()
        # Remove temp dir
        shutil.rmtree(work_dir, ignore_errors=True)


def _docker_cleanup() -> None:
    """Stop all containers, remove project networks, and prune."""
    log.info("cleaning up docker state...")
    cmds = [
        # Stop all running containers
        ["docker", "stop", "-t", "5"],
        # Remove all stopped containers
        ["docker", "container", "prune", "-f"],
        # Remove project networks (not default ones)
        ["docker", "network", "prune", "-f"],
        # Remove dangling volumes
        ["docker", "volume", "prune", "-f"],
    ]

    # Get running container IDs first; docker stop needs at least one arg
    try:
        result = subprocess.run(
            ["docker", "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=DOCKER_CLEANUP_TIMEOUT,
        )
        container_ids = result.stdout.strip().split()
    except Exception:
        container_ids = []

    if container_ids:
        _run_cleanup_cmd(["docker", "stop", "-t", "5"] + container_ids)

    for cmd in cmds[1:]:
        _run_cleanup_cmd(cmd)

    log.info("docker cleanup done")


def _run_cleanup_cmd(cmd: list[str]) -> None:
    """Run a cleanup command, swallowing errors."""
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOCKER_CLEANUP_TIMEOUT,
        )
    except Exception as e:
        log.warning("cleanup cmd failed: {} — {}", " ".join(cmd), e)


def _build_summary(config: dict, results: list[dict]) -> dict:
    """Build an aggregate summary from per-app results."""
    total = len(results)
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    times = [r["time_seconds"] for r in results]
    success_times = [r["time_seconds"] for r in successes]
    attempts = [r["attempts"] for r in results if r["attempts"] > 0]

    total_tokens = sum(r["tokens"]["total_tokens"] for r in results)
    total_cost = sum(r["tokens"]["total_cost"] for r in results)
    total_calls = sum(r["tokens"]["num_calls"] for r in results)

    return {
        "config": config,
        "aggregate": {
            "total_apps": total,
            "successes": len(successes),
            "failures": len(failures),
            "success_rate": round(len(successes) / total, 2) if total else 0,
            "time": {
                "total_seconds": round(sum(times), 1),
                "mean_seconds": round(sum(times) / total, 1) if total else 0,
                "mean_success_seconds": (round(sum(success_times) / len(success_times), 1) if success_times else None),
                "min_seconds": round(min(times), 1) if times else 0,
                "max_seconds": round(max(times), 1) if times else 0,
            },
            "attempts": {
                "mean": round(sum(attempts) / len(attempts), 2) if attempts else 0,
                "max": max(attempts) if attempts else 0,
            },
            "tokens": {
                "total": total_tokens,
                "mean_per_app": round(total_tokens / total) if total else 0,
                "total_cost": round(total_cost, 4),
            },
            "llm_calls": {
                "total": total_calls,
                "mean_per_app": round(total_calls / total) if total else 0,
            },
        },
        "per_app": [
            {
                "app": r["app"],
                "success": r["success"],
                "phase": r["phase"],
                "attempts": r["attempts"],
                "time_seconds": r["time_seconds"],
                "total_tokens": r["tokens"]["total_tokens"],
                "cost": r["tokens"]["total_cost"],
                "error": r.get("error"),
            }
            for r in results
        ],
    }


def _log_result(result: dict) -> None:
    status = "SUCCESS" if result["success"] else "FAILED"
    log.info(
        "{} — {} | phase={} attempts={} time={:.1f}s tokens={}",
        result["app"],
        status,
        result["phase"],
        result["attempts"],
        result["time_seconds"],
        result["tokens"]["total_tokens"],
    )


def _print_summary_table(summary: dict) -> None:
    agg = summary["aggregate"]
    apps = summary["per_app"]

    log.info("")
    log.info("=" * 80)
    log.info("EXPERIMENT SUMMARY")
    log.info("=" * 80)
    log.info(
        "Success rate: {}/{} ({:.0f}%)",
        agg["successes"],
        agg["total_apps"],
        agg["success_rate"] * 100,
    )
    log.info("Mean time: {:.1f}s", agg["time"]["mean_seconds"])
    if agg["time"]["mean_success_seconds"] is not None:
        log.info("Mean time (successes): {:.1f}s", agg["time"]["mean_success_seconds"])
    log.info("Mean attempts: {:.1f}", agg["attempts"]["mean"])
    log.info("Total tokens: {} (mean {}/app)", agg["tokens"]["total"], agg["tokens"]["mean_per_app"])
    log.info("Total cost: ${:.4f}", agg["tokens"]["total_cost"])
    log.info("Total LLM calls: {} (mean {}/app)", agg["llm_calls"]["total"], agg["llm_calls"]["mean_per_app"])
    log.info("")

    # Per-app table
    header = f"{'App':<25} {'Result':<8} {'Phase':<12} {'Att':>3} {'Time':>7} {'Tokens':>8} {'Cost':>8}"
    log.info(header)
    log.info("-" * len(header))
    for app in apps:
        status = "PASS" if app["success"] else "FAIL"
        log.info(
            "{:<25} {:<8} {:<12} {:>3} {:>6.1f}s {:>8} ${:>6.4f}",
            app["app"][:25],
            status,
            app["phase"],
            app["attempts"],
            app["time_seconds"],
            app["total_tokens"],
            app["cost"],
        )
    log.info("-" * len(header))


def _write_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
