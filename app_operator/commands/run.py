import argparse
import sys

from app_operator.operator import AppOperator
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.config import load_config


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'run' command."""
    parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory path of the repository to deploy"
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)"
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' command logic."""
    if not args.directory:
        # This case should ideally be handled by argparse if 'directory' was required
        # but including for robustness.
        print(
            "Error: Directory not specified for 'run' command.",
            file=sys.stderr)
        return 1

    config = load_config(args.directory, args.config)
    interval = config.operator.interval

    if interval < 1:
        print("Error: interval must be at least 1 second", file=sys.stderr)
        return 1
    try:
        agent = create_agent_from_config(
            args.directory,
            config=config
        )
        operator = AppOperator(
            repo_path=args.directory,
            health_check_interval=interval,
            agent=agent
        )

        return operator.run()

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    except Exception as e:
        print(f"✗ Unexpected error: {e}", file=sys.stderr)
        return 1
