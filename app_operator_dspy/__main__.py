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
from app_operator_dspy.operator import DSPyOperator, configure_lm


def run_command(args: argparse.Namespace) -> int:
    source_app = os.path.abspath(args.app_path)
    if not os.path.isdir(source_app):
        print(f"[error] App not found: {source_app}")
        return 1

    app_name = os.path.basename(source_app)

    # Copy app to a temp dir so we don't pollute the repo
    work_dir = tempfile.mkdtemp(prefix="sds_dspy_")
    atexit.register(shutil.rmtree, work_dir, True)
    repo_path = os.path.join(work_dir, app_name)
    shutil.copytree(source_app, repo_path)
    print(f"[setup] App: {app_name}")
    print(f"[setup] Working directory: {repo_path}")

    # Configure LM
    model = args.model or DEFAULT_MODEL
    lm_kwargs = get_lm_kwargs(model)
    print(f"[setup] Model: {model}")
    configure_lm(model, **lm_kwargs)

    # Run operator
    print("[run] Creating DSPyOperator...")
    operator = DSPyOperator()

    print(f"[run] Starting operator on {repo_path}")
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
        print(f"\n[ERROR] Operator failed with exception: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"[result] Completed in {elapsed:.1f}s")
    print(f"[result] Success: {result.success}")
    print(f"[result] Phase: {result.phase}")

    if result.success:
        print(f"[result] Monitor statuses: {result.statuses}")
    else:
        print(f"[result] Error: {getattr(result, 'error', 'N/A')}")

    _print_artifacts(repo_path)
    return 0 if result.success else 1


def _print_artifacts(repo_path: str) -> None:
    sds_dir = os.path.join(repo_path, ".sds")
    if not os.path.isdir(sds_dir):
        return

    print(f"\n[artifacts] Files in {sds_dir}:")
    for root, _dirs, files in os.walk(sds_dir):
        for f in files:
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            rel = os.path.relpath(fpath, sds_dir)
            print(f"  .sds/{rel} ({size} bytes)")

    for script in ["code_analysis.md", "deploy.sh", "health_check.sh"]:
        spath = os.path.join(sds_dir, script)
        if os.path.isfile(spath):
            with open(spath) as fh:
                content = fh.read()
            print(f"\n{'=' * 60}")
            print(f"[artifact] .sds/{script}:")
            print(f"{'=' * 60}")
            print(content[:2000])
            if len(content) > 2000:
                print(f"... ({len(content) - 2000} more chars)")


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
