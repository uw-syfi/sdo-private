"""CLI entry point for the operator module."""

from app_operator.coding_agent_mode import CodingAgentOperator
from app_operator.script_generator import generate_scripts, create_agent_from_config
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
  # Run Codex-assisted deployment on a repository
  python -m app_operator run /path/to/repository

  # Use a custom health check interval
  python -m app_operator run /path/to/repository --interval 60

  # Generate deployment scripts for a repository
  python -m app_operator generate-scripts /path/to/repository
        """)

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands")

    subparsers.required = True
    run_parser = subparsers.add_parser(
        "run", help="Run Codex-assisted deployment on a repository")

    run_parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory path of the repository to deploy"
    )
    run_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
    )
    run_parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Model to use (default: from config or env var)"
    )
    run_parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to configuration file (default: sds.toml in target dir)"
    )

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

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, 'run')

    args = parser.parse_args()
    if args.command == "run":
        if not args.directory:
            parser.print_help()
            return 1
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

    elif args.command == "generate-scripts":
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


if __name__ == "__main__":
    sys.exit(main())
