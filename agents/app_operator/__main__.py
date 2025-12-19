"""CLI entry point for the operator module."""

import argparse
import sys

from app_operator.app_registry import registry
from app_operator.operator import ApplicationOperator


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
  python -m operator --list

  # Deploy and monitor the hotel application
  python -m operator --app-name hotel

  # Use custom health check interval (default: 10 seconds)
  python -m operator --app-name hotel --interval 30

  # Stop the application with Ctrl+C
        """
    )
    
    parser.add_argument(
        "--app-name", "-a",
        metavar="NAME",
        help="Name of the application to deploy and monitor"
    )
    
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available applications"
    )
    
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Interval between health checks in seconds (default: 10)"
    )
    
    args = parser.parse_args()
    
    # Handle --list command
    if args.list:
        print_available_applications()
        return 0
    
    # Require --app-name if not listing
    if not args.app_name:
        parser.error("the following arguments are required: --app-name/-a")
    
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
        print("Use --list to see available applications.", file=sys.stderr)
        return 1
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Please ensure all required files are present.", file=sys.stderr)
        return 1
        
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
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
