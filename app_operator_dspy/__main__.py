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
from app_operator_dspy.tools.context import set_task_repo

log = get_logger("main")


def run_command(args: argparse.Namespace) -> int:
    setup_logger()
    source_app = os.path.abspath(args.app_path)
    if not os.path.isdir(source_app):
        log.error("App not found: {}", source_app)
        return 1

    app_name = os.path.basename(source_app)

    # Copy app to a temp dir so we don't pollute the repo
    work_dir = tempfile.mkdtemp(prefix="sds_dspy_")
    atexit.register(shutil.rmtree, work_dir, True)
    repo_path = os.path.join(work_dir, app_name)
    shutil.copytree(source_app, repo_path)
    log.info("App: {}", app_name)
    log.info("Working directory: {}", repo_path)

    # Configure LM
    model = args.model or DEFAULT_MODEL
    lm_kwargs = get_lm_kwargs(model)
    log.info("Model: {}", model)
    configure_lm(model, **lm_kwargs)

    # Run operator
    log.info("Creating DSPyOperator...")
    operator = DSPyOperator()

    set_task_repo(repo_path)
    log.info("Starting operator on {}", repo_path)
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
        log.error("Operator failed with exception: {}: {}", type(e).__name__, e)
        traceback.print_exc()
        return 1

    elapsed = time.time() - start
    log.info("")
    log.info("{}", "=" * 60)
    log.info("Completed in {:.1f}s", elapsed)
    log.info("Success: {}", result.success)
    log.info("Phase: {}", result.phase)

    if result.success:
        log.info("Monitor statuses: {}", result.statuses)
    else:
        log.info("Error: {}", getattr(result, "error", "N/A"))

    _log_artifacts(repo_path)
    return 0 if result.success else 1


def _log_artifacts(repo_path: str) -> None:
    sds_dir = os.path.join(repo_path, ".sds")
    if not os.path.isdir(sds_dir):
        return

    log.info("")
    log.info("Files in {}:", sds_dir)
    for root, _dirs, files in os.walk(sds_dir):
        for f in files:
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            rel = os.path.relpath(fpath, sds_dir)
            log.info("  .sds/{} ({} bytes)", rel, size)

    for script in ["code_analysis.md", "deploy.sh", "health_check.sh"]:
        spath = os.path.join(sds_dir, script)
        if os.path.isfile(spath):
            with open(spath) as fh:
                content = fh.read()
            log.info("")
            log.info("{}", "=" * 60)
            log.info(".sds/{}:", script)
            log.info("{}", "=" * 60)
            log.info("{}", content[:2000])
            if len(content) > 2000:
                log.info("... ({} more chars)", len(content) - 2000)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by ``run`` and ``experiment``."""
    parser.add_argument("--model", help=f"LiteLLM model identifier (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-attempts", type=int, default=5, help="Max deploy attempts (default: 5)")
    parser.add_argument("--monitor-checks", type=int, default=2, help="Number of monitor checks (default: 2)")
    parser.add_argument(
        "--deploy-timeout",
        type=int,
        default=DEPLOY_TIMEOUT,
        help=f"Deploy timeout in seconds (default: {DEPLOY_TIMEOUT})",
    )
    parser.add_argument(
        "--health-check-timeout",
        type=int,
        default=HEALTH_CHECK_TIMEOUT,
        help=f"Health check timeout in seconds (default: {HEALTH_CHECK_TIMEOUT})",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="app_operator_dspy",
        description="DSPy-native application operator.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    # -- run ---------------------------------------------------------------
    run_parser = subparsers.add_parser("run", help="Deploy an application")
    run_parser.add_argument("app_path", help="Path to the application directory")
    _add_common_args(run_parser)

    # -- experiment --------------------------------------------------------
    exp_parser = subparsers.add_parser(
        "experiment",
        help="Run baseline experiment across multiple apps",
    )
    exp_parser.add_argument(
        "app_paths",
        nargs="+",
        help="Paths to application directories",
    )
    exp_parser.add_argument(
        "--results-dir",
        default="results/dspy_baseline",
        help="Directory to store experiment results (default: results/dspy_baseline)",
    )
    _add_common_args(exp_parser)

    # -- optimize ----------------------------------------------------------
    opt_parser = subparsers.add_parser(
        "optimize",
        help="Optimize signature instructions with DSPy optimizers or GEPA",
    )
    opt_parser.add_argument(
        "optimizer",
        choices=["bootstrap", "mipro", "copro", "gepa"],
        help="Optimization method",
    )
    opt_parser.add_argument(
        "--signatures",
        nargs="+",
        default=["GenerateDeployScript", "RepairDeploymentError"],
        help="Signatures to optimize (default: GenerateDeployScript RepairDeploymentError)",
    )
    opt_parser.add_argument(
        "--train-apps",
        nargs="+",
        help="Training app paths (default: built-in train split)",
    )
    opt_parser.add_argument(
        "--eval-apps",
        nargs="+",
        help="Eval app paths (default: built-in eval split)",
    )
    opt_parser.add_argument(
        "--output-dir",
        default="results/optimized",
        help="Where to save optimized state (default: results/optimized)",
    )
    opt_parser.add_argument("--model", help=f"LiteLLM model identifier (default: {DEFAULT_MODEL})")
    opt_parser.add_argument("--no-seeds", action="store_true", help="Skip seed candidates (GEPA only)")

    # Shortcut: bare path without subcommand → run
    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices and not sys.argv[1].startswith("-"):
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    if args.command == "run":
        return run_command(args)

    if args.command == "experiment":
        from app_operator_dspy.experiment import run_experiment

        result_dir = run_experiment(args)
        return 0 if result_dir else 1

    if args.command == "optimize":
        return _optimize_command(args)

    parser.print_help()
    return 1


def _optimize_command(args: argparse.Namespace) -> int:
    setup_logger()

    model = args.model or DEFAULT_MODEL
    lm_kwargs = get_lm_kwargs(model)
    configure_lm(model, **lm_kwargs)
    log.info("Optimizer: {}", args.optimizer)
    log.info("Model: {}", model)
    log.info("Signatures: {}", args.signatures)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.optimizer == "gepa":
        from app_operator_dspy.optimize.dataset import EVAL_APPS, TRAIN_APPS
        from app_operator_dspy.optimize.gepa.optimizer import GEPAConfig, run_gepa

        train_paths = args.train_apps or [os.path.join(repo_root, p) for p in TRAIN_APPS]
        eval_paths = args.eval_apps or [os.path.join(repo_root, p) for p in EVAL_APPS]

        result = run_gepa(
            signature_names=args.signatures,
            train_app_paths=train_paths,
            eval_app_paths=eval_paths,
            config=GEPAConfig(
                output_dir=args.output_dir,
                use_seeds=not getattr(args, "no_seeds", False),
            ),
        )
        log.info("GEPA: {:.3f} → {:.3f}", result.score_before, result.score_after)
    else:
        from app_operator_dspy.optimize.dataset import get_eval_examples, get_train_examples
        from app_operator_dspy.optimize.dspy_optimizers import compile_with_optimizer

        trainset = get_train_examples(repo_root)
        valset = get_eval_examples(repo_root)

        if args.train_apps:
            from app_operator_dspy.optimize.dataset import make_examples

            trainset = make_examples(args.train_apps)
        if args.eval_apps:
            from app_operator_dspy.optimize.dataset import make_examples

            valset = make_examples(args.eval_apps)

        result = compile_with_optimizer(
            optimizer_name=args.optimizer,
            trainset=trainset,
            valset=valset,
            output_dir=args.output_dir,
        )
        log.info("{}: {:.3f} → {:.3f}", result.optimizer_name, result.score_before, result.score_after)

    return 0


if __name__ == "__main__":
    sys.exit(main())
