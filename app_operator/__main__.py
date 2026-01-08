import argparse
import sys

from dotenv import load_dotenv

from app_operator.commands import run, generate_scripts

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

    # 'run' command
    run_parser = subparsers.add_parser(
        "run", help="Run Codex-assisted deployment on a repository")
    run.add_arguments(run_parser)

    # 'generate-scripts' command
    gen_parser = subparsers.add_parser(
        "generate-scripts",
        help="Generate deploy.sh and health_check.sh scripts for a repository"
    )
    generate_scripts.add_arguments(gen_parser)

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, 'run')

    args = parser.parse_args()
    if args.command == "run":
        return run.run_command(args)
    elif args.command == "generate-scripts":
        return generate_scripts.generate_scripts_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
