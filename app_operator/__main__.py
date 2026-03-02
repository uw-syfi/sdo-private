import argparse
import shutil
import subprocess
import sys

from dotenv import load_dotenv

from app_operator.commands import (
    run,
    init_exp,
    analyze_prompts,
    optimize_prompts,
    e2e_optimize,
    run_exp,
    plot_exp,
)
import app_operator.langgraph.viz_graph as viz_graph
from app_operator.logger import logger

# Load environment variables from .env file
load_dotenv()

REQUIRED_DEPENDENCIES = ["docker", "kubectl"]

INSTALL_HINTS = {
    "docker": "Install Docker: https://docs.docker.com/engine/install/",
    "kubectl": "Install kubectl: https://kubernetes.io/docs/tasks/tools/",
}


def check_dependencies():
    """Check if required system dependencies are installed."""
    missing = []
    for tool in REQUIRED_DEPENDENCIES:
        if not shutil.which(tool):
            missing.append(tool)

    if missing:
        for tool in missing:
            hint = INSTALL_HINTS.get(tool, f"Please install '{tool}' to continue.")
            logger.error(f"Missing required dependency '{tool}': {hint}")
        sys.exit(1)

    # Check if docker daemon is running
    try:
        subprocess.check_call(
            ["docker", "container", "ls"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        logger.error("Docker daemon is not running or not accessible.")
        logger.info("Start Docker and try again: https://docs.docker.com/engine/install/")
        sys.exit(1)


def main() -> int:
    """Main entry point for the operator CLI.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="operator",
        description="Codex-assisted deployment mode with automatic error fixing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run Codex-assisted deployment on a repository
  ./sds_operator /path/to/repository

  # Use a custom health check interval
  ./sds_operator /path/to/repository --interval 60
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.required = True

    # 'run' command
    run_parser = subparsers.add_parser(
        "run", help="Run Codex-assisted deployment on a repository"
    )
    run.add_arguments(run_parser)

    # 'init-exp' command
    init_exp_parser = subparsers.add_parser(
        "init-exp", help="Initialize a new experiment from an existing application"
    )
    init_exp.add_arguments(init_exp_parser)

    # 'viz-graph' command
    viz_graph_parser = subparsers.add_parser(
        "viz-graph", help="Visualize the agent's dependency graph"
    )
    viz_graph.add_arguments(viz_graph_parser)

    # 'analyze-prompts' command
    analyze_prompts_parser = subparsers.add_parser(
        "analyze-prompts", help="Analyze prompt performance from trajectory data"
    )
    analyze_prompts.add_arguments(analyze_prompts_parser)

    # 'optimize-prompts' command
    optimize_prompts_parser = subparsers.add_parser(
        "optimize-prompts", help="Optimize prompts using DSPy"
    )
    optimize_prompts.add_arguments(optimize_prompts_parser)

    # 'e2e-optimize' command
    e2e_optimize_parser = subparsers.add_parser(
        "e2e-optimize", help="Run end-to-end optimization loop"
    )
    e2e_optimize.add_arguments(e2e_optimize_parser)

    # 'run-exp' command
    run_exp_parser = subparsers.add_parser(
        "run-exp", help="Run experiments defined in a TOML config file"
    )
    run_exp.add_arguments(run_exp_parser)

    # 'plot-exp' command
    plot_exp_parser = subparsers.add_parser(
        "plot-exp", help="Plot and compare experiment results"
    )
    plot_exp.add_arguments(plot_exp_parser)

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    # Check Docker dependencies for commands that need them
    if args.command in ["run", "init-exp"]:
        check_dependencies()

    if args.command == "run":
        return run.run_command(args)
    elif args.command == "init-exp":
        return init_exp.run_command(args)
    elif args.command == "viz-graph":
        return viz_graph.run_command(args)
    elif args.command == "analyze-prompts":
        return analyze_prompts.run_command(args)
    elif args.command == "optimize-prompts":
        return optimize_prompts.run_command(args)
    elif args.command == "e2e-optimize":
        return e2e_optimize.run_command(args)
    elif args.command == "run-exp":
        return run_exp.run_command(args)
    elif args.command == "plot-exp":
        return plot_exp.run_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
