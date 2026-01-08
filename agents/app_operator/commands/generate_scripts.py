import argparse
import sys

from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.script_generator import generate_scripts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add arguments specific to the 'generate-scripts' command."""
    parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory path of the repository to generate scripts for"
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Model to use (default: from config or env var)"
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)"
    )


def generate_scripts_command(args: argparse.Namespace) -> int:
    """Execute the 'generate-scripts' command logic."""
    try:
        agent = create_agent_from_config(
            args.directory,
            model_override=args.model,
            config_path=args.config
        )

        success, message = generate_scripts(args.directory, agent)

        if success:
            print(f"✓ {message}")
            return 0
        else:
            print(f"✗ {message}", file=sys.stderr)
            return 1

    except Exception as e:
        print(f"✗ Unexpected error: {e}", file=sys.stderr)
        return 1
