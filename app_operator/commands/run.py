import argparse

from app_operator.operator import AppOperator
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.config import load_config
from app_operator.logger import logger


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'run' command."""
    parser.add_argument(
        "directory", metavar="DIR", help="Directory path of the repository to deploy"
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)",
    )


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' command logic."""
    if not args.directory:
        # This case should ideally be handled by argparse if 'directory' was required
        # but including for robustness.
        logger.error("Error: Directory not specified for 'run' command.")
        return 1

    config = load_config(args.directory, args.config)
    interval = config.operator.interval

    if interval < 1:
        logger.error("Error: interval must be at least 1 second")
        return 1
    try:
        agent = create_agent_from_config(args.directory, config=config)
        operator = AppOperator(
            repo_path=args.directory,
            health_check_interval=interval,
            health_check_max_count=config.operator.monitoring_max_iters,
            max_deployment_attempts=config.operator.deployment_max_iters,
            agent=agent,
        )

        return operator.run()

    except ValueError as e:
        logger.error(f"Error: {e}")
        return 1

    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        return 1
