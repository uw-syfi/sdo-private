import argparse
import sys

from dotenv import load_dotenv

from app_operator.commands import run

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
        """)

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands")

    subparsers.required = True

    # 'run' command
    run_parser = subparsers.add_parser(
        "run", help="Run Codex-assisted deployment on a repository")
    run.add_arguments(run_parser)

    if len(sys.argv) > 1 and sys.argv[1] not in subparsers.choices:
        sys.argv.insert(1, 'run')

    args = parser.parse_args()
    if args.command == "run":
        return run.run_command(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
