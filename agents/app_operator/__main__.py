"""CLI entry point for the operator module."""

import argparse
import sys

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from app_operator.app_registry import registry
from app_operator.operator import ApplicationOperator
from app_operator.script_generator import generate_scripts, CodexCodingAgent
from app_operator.coding_agent_mode import CodingAgentOperator


def main() -> int:
    """Main entry point for the operator CLI.
    
    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="operator",
        description="Deploy and monitor applications with automated health checks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available applications
  python -m app_operator list
  python -m app_operator --list  # legacy

  # Deploy and monitor the hotel application
  python -m app_operator run --app-name hotel
  python -m app_operator --app-name hotel  # legacy

  # Use custom health check interval
  python -m app_operator run --app-name hotel --interval 30

  # Generate deployment scripts for a repository
  python -m app_operator generate-scripts /path/to/repository

  # Codex-assisted deployment mode
  python -m app_operator codex /path/to/repository

  # Stop the application with Ctrl+C
        """
    )
    
    # Legacy arguments for backward compatibility
    parser.add_argument(
        "--app-name", "-a",
        metavar="NAME",
        help="Name of the application to deploy and monitor (legacy, use 'run' subcommand)"
    )
    
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available applications (legacy, use 'list' subcommand)"
    )
    
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Deploy and monitor an application")
    run_parser.add_argument(
        "--app-name", "-a",
        metavar="NAME",
        required=True,
        help="Name of the application to deploy and monitor"
    )
    run_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
    )
    
    # List command
    subparsers.add_parser("list", help="List all available applications")
    
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
        help="Codex model to use (default: from CODEX_MODEL env var or gpt-4o-mini)"
    )
    
    # Codex mode command
    codex_parser = subparsers.add_parser(
        "codex",
        help="Codex-assisted deployment mode with automatic error fixing"
    )
    codex_parser.add_argument(
        "directory",
        metavar="DIR",
        help="Directory path of the repository to deploy"
    )
    codex_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 30)"
    )
    codex_parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Codex model to use (default: from CODEX_MODEL env var or gpt-4o-mini)"
    )
    
    args = parser.parse_args()
    
    # Handle legacy --list flag
    if args.list:
        print_available_applications()
        return 0
    
    # Determine command
    command = args.command
    
    # If no command specified but --app-name is provided, use legacy run behavior
    if not command and args.app_name:
        command = "run"
        # Create a namespace-like object for legacy compatibility
        class LegacyArgs:
            app_name = args.app_name
            interval = args.interval
        args = LegacyArgs()
    
    # If no command and no --app-name, show help
    if not command:
        parser.print_help()
        return 0
    
    # Handle commands
    if command == "list":
        print_available_applications()
        return 0
    
    elif command == "run":
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
            print("Please ensure all required files are present.", file=sys.stderr)
            return 1
            
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            return 1
    
    elif command == "generate-scripts":
        try:
            agent = CodexCodingAgent(model=args.model)
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
    
    elif command == "codex":
        # Validate interval
        if args.interval < 1:
            parser.error("interval must be at least 1 second")
        
        try:
            agent = CodexCodingAgent(model=args.model)
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
    
    else:
        parser.print_help()
        return 1


def print_available_applications():
    """Print a formatted list of available applications."""
    print("\nAvailable Applications")
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
    print("\nUsage: python -m operator --app-name <NAME>\n")


if __name__ == "__main__":
    sys.exit(main())
