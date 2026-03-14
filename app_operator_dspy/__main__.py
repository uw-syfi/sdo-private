"""CLI entry point: ``uv run -m app_operator_dspy run <app_path>``."""

import argparse
import atexit
import os
import shutil
import sys
import tempfile
import time
import traceback

from app_operator_dspy.constants import DEPLOY_TIMEOUT, HEALTH_CHECK_TIMEOUT
from app_operator_dspy.lm_config import DEFAULT_MODEL, get_lm_kwargs
from app_operator_dspy.logger import get_logger, setup_logger
from app_operator_dspy.operator import DSPyOperator, configure_lm

log = get_logger("main")


def run_command(args: argparse.Namespace) -> int:
    setup_logger()
    source_app = os.path.abspath(args.app_path)
    if not os.path.isdir(source_app):
        log.error("App not found: %s", source_app)
        return 1

    app_name = os.path.basename(source_app)

    # Copy app to a temp dir so we don't pollute the repo
    work_dir = tempfile.mkdtemp(prefix="sds_dspy_")
    atexit.register(shutil.rmtree, work_dir, True)
    repo_path = os.path.join(work_dir, app_name)
    shutil.copytree(source_app, repo_path)
    log.info("App: %s", app_name)
    log.info("Working directory: %s", repo_path)

    # Configure LM
    model = args.model or DEFAULT_MODEL
    lm_kwargs = get_lm_kwargs(model)
    log.info("Model: %s", model)
    configure_lm(model, **lm_kwargs)

    # Run operator
    log.info("Creating DSPyOperator...")
    operator = DSPyOperator()

    log.info("Starting operator on %s", repo_path)
    start = time.time()
    try:
        result = operator(
            repo_path=repo_path,
            max_deploy_attempts=args.max_attempts,
            monitor_checks=args.monitor_checks,
            deploy_timeout=args.deploy_timeout,
            health_check_timeout=args.health_check_timeout,
        )
    except Exception as e:
        log.error("Operator failed with exception: %s: %s", type(e).__name__, e)
        traceback.print_exc()
        return 1

    elapsed = time.time() - start
    log.info("")
    log.info("%s", "=" * 60)
    log.info("Completed in %.1fs", elapsed)
    log.info("Success: %s", result.success)
    log.info("Phase: %s", result.phase)

    if result.success:
        log.info("Monitor statuses: %s", result.statuses)
    else:
        log.info("Error: %s", getattr(result, "error", "N/A"))

    _log_artifacts(repo_path)
    return 0 if result.success else 1


def _log_artifacts(repo_path: str) -> None:
    sds_dir = os.path.join(repo_path, ".sds")
    if not os.path.isdir(sds_dir):
        return

    log.info("")
    log.info("Files in %s:", sds_dir)
    for root, _dirs, files in os.walk(sds_dir):
        for f in files:
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            rel = os.path.relpath(fpath, sds_dir)
            log.info("  .sds/%s (%d bytes)", rel, size)

    for script in ["code_analysis.md", "deploy.sh", "health_check.sh"]:
        spath = os.path.join(sds_dir, script)
        if os.path.isfile(spath):
            with open(spath) as fh:
                content = fh.read()
            log.info("")
            log.info("%s", "=" * 60)
            log.info(".sds/%s:", script)
            log.info("%s", "=" * 60)
            log.info("%s", content[:2000])
            if len(content) > 2000:
                log.info("... (%d more chars)", len(content) - 2000)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="app_operator_dspy",
        description="DSPy-native application operator.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    run_parser = subparsers.add_parser("run", help="Deploy an application")
    run_parser.add_argument("app_path", help="Path to the application directory")
    run_parser.add_argument("--model", help=f"LiteLLM model identifier (default: {DEFAULT_MODEL})")
    run_parser.add_argument("--max-attempts", type=int, default=5, help="Max deploy attempts (default: 5)")
    run_parser.add_argument("--monitor-checks", type=int, default=2, help="Number of monitor checks (default: 2)")
    run_parser.add_argument(
        "--deploy-timeout",
        type=int,
        default=DEPLOY_TIMEOUT,
        help=f"Deploy timeout in seconds (default: {DEPLOY_TIMEOUT})",
    )
    run_parser.add_argument(
        "--health-check-timeout",
        type=int,
        default=HEALTH_CHECK_TIMEOUT,
        help=f"Health check timeout in seconds (default: {HEALTH_CHECK_TIMEOUT})",
    )

    # Shortcut: bare path without subcommand → run
    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices and not sys.argv[1].startswith("-"):
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    if args.command == "run":
        return run_command(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
