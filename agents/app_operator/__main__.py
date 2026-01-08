"""CLI entry point for the operator module."""

from app_operator.coding_agent_mode import CodingAgentOperator
from app_operator.script_generator import generate_scripts, CodexCodingAgent, create_agent_from_config
from app_operator.operator import ApplicationOperator
from app_operator.app_registry import registry
import argparse
import sys

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


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
  # Run Codex-assisted deployment on a repository (default command)
  python -m app_operator /path/to/repository

  # Use a custom health check interval
  python -m app_operator /path/to/repository --interval 60

  # List available applications for legacy mode
  python -m app_operator list

  # Run legacy deployment for the hotel application
  python -m app_operator legacy --app-name hotel

  # Generate deployment scripts for a repository
  python -m app_operator generate-scripts /path/to/repository
        """
    )

    parser.add_argument(
        "directory",
        metavar="DIR",
        nargs='?',
        default=None,
        help="Directory path of the repository to deploy (default if no subcommand is used)"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
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

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands")

    # Legacy command (previously 'run')
    legacy_parser = subparsers.add_parser(
        "legacy", help="Deploy and monitor a registered application (legacy mode)")
    legacy_parser.add_argument(
        "--app-name", "-a",
        metavar="NAME",
        required=True,
        help="Name of the application to deploy and monitor"
    )
    legacy_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
    )

    # List command
    subparsers.add_parser(
        "list", help="List all available applications for legacy mode")

    # Generate scripts command
    gen_parser = subparsers.add_parser(
        "generate-scripts",
        help="Generate deploy.sh and health_check.sh scripts for a repository"
    )
    gen_parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory path of the repository to generate scripts for"
    )
    gen_parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Model to use (default: from config or env var)"
    )
    gen_parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)"
    )

    args = parser.parse_args()

    command = args.command

    # If no command, it's the default (codex) mode
    if command is None:
        if not args.directory:
            parser.print_help()
            return 1

        # Validate interval
        if args.interval < 1:
            parser.error("interval must be at least 1 second")

        try:
            agent = create_agent_from_config(
                args.directory,
                model_override=args.model,
                config_path=args.config
            )
            operator = CodingAgentOperator(
                repo_path=args.directory,
                health_check_interval=args.interval,
                agent=agent
            )
            return operator.run()
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"✗ Unexpected error: {e}", file=sys.stderr)
            return 1

    # Handle subcommands
    if command == "list":
        print_available_applications()
        return 0

    elif command == "legacy":
        # Validate interval
        if args.interval < 1:
            parser.error("interval must be at least 1 second")

        # Run the operator
        try:
            app = registry.get(args.app_name)
            operator = ApplicationOperator(app, check_interval=args.interval)
            return operator.run()

        except ValueError as e:
            print(f"Error: {e}\n", file=sys.stderr)
            print("Use 'list' to see available applications.", file=sys.stderr)
            return 1

        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Please ensure all required files are present.",
                file=sys.stderr)
            return 1

        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 1

    elif command == "generate-scripts":
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

    else:
        parser.print_help()
        return 1


def print_available_applications():
    """Print a formatted list of available applications."""
    print("\nAvailable Applications for Legacy Mode")
    print("=" * 70)

    descriptions = registry.get_descriptions()

    if not descriptions:
        print("  No applications registered.")
        return

    # Find the longest name for formatting
    max_name_length = max(len(name) for name in descriptions.keys())

    for name, description in sorted(descriptions.items()):
        print(f"  {name:<{max_name_length}}  -  {description}")

    print(f"\nTotal: {len(descriptions)} application(s)")
    print("=" * 70)
    print("\nUsage: python -m app_operator legacy --app-name <NAME>\n")


if __name__ == "__main__":
    sys.exit(main())
