import argparse
import shutil
import subprocess
import sys

from dotenv import load_dotenv

from app_operator.commands import run, init_exp, viz_graph
from app_operator.logger import logger

# Load environment variables from .env file
load_dotenv()

REQUIRED_DEPENDENCIES = ["docker", "kubectl"]


def check_dependencies():
    """Check if required system dependencies are installed."""
    missing = []
    for tool in REQUIRED_DEPENDENCIES:
        if not shutil.which(tool):
            missing.append(tool)

    if missing:
        logger.error(f"Missing required system dependencies: {', '.join(missing)}")
        logger.info("Please install them to continue.")
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
        logger.info("Please start Docker to continue.")
        sys.exit(1)


def main() -> int:
    """Main entry point for the operator CLI.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    check_dependencies()

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

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, "run")

    args = parser.parse_args()
    if args.command == "run":
        return run.run_command(args)
    elif args.command == "init-exp":
        return init_exp.run_command(args)
    elif args.command == "viz-graph":
        return viz_graph.run_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
