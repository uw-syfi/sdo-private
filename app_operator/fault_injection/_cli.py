"""Standalone CLI for fault injection.

Provides inject, revert, and list subcommands for shell script integration.

Usage:
    python -m app_operator.fault_injection._cli list
    python -m app_operator.fault_injection._cli inject --repo-path /path/to/app
    python -m app_operator.fault_injection._cli revert --repo-path /path/to/app
"""

import argparse
import sys
from pathlib import Path

from app_operator.fault_injection.compose_faults import COMPOSE_FAULTS
from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.injector import FaultInjectionOrchestrator


def cmd_list(args: argparse.Namespace) -> int:
    """List all available faults."""
    for fault in COMPOSE_FAULTS:
        print(
            f"{fault.fault_id:10s} {fault.name:30s} "
            f"{fault.category.value:18s} {fault.severity.value:8s} "
            f"{fault.description}"
        )
    print(f"\nTotal: {len(COMPOSE_FAULTS)} faults")
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    """Inject faults into a repository's compose file."""
    repo_path = Path(args.repo_path)
    if not repo_path.is_dir():
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        return 1

    config = FaultInjectionConfig(
        enabled=True,
        num_faults=args.num_faults,
        categories=args.categories or [],
        severities=args.severities or [],
        seed=args.seed,
    )

    orchestrator = FaultInjectionOrchestrator(config=config)
    try:
        results = orchestrator.inject(repo_path, seed=args.seed)
    except (FileNotFoundError, ValueError, ImportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"Injected {len(successful)} fault(s):")
    for r in successful:
        print(f"  {r.fault.fault_id}: {r.fault.name} -> {r.target_service}")

    if failed:
        print(f"\nFailed {len(failed)} injection(s):")
        for r in failed:
            print(f"  {r.fault.fault_id}: {r.error_message}")

    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    """Revert fault injection from a repository."""
    repo_path = Path(args.repo_path)
    if not repo_path.is_dir():
        print(f"Error: {repo_path} is not a directory", file=sys.stderr)
        return 1

    orchestrator = FaultInjectionOrchestrator()
    if orchestrator.revert(repo_path):
        print("Compose file restored from backup")
        return 0
    print("No backup found to restore", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="SDS Fault Injection CLI",
        prog="python -m app_operator.fault_injection._cli",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="List all available faults")

    # inject
    inject_parser = subparsers.add_parser("inject", help="Inject faults")
    inject_parser.add_argument("--repo-path", required=True, help="Path to the repository")
    inject_parser.add_argument("--num-faults", type=int, default=2, help="Number of faults (1-5)")
    inject_parser.add_argument("--seed", type=int, default=None, help="Random seed")
    inject_parser.add_argument(
        "--categories",
        nargs="*",
        default=None,
        help="Fault categories to include",
    )
    inject_parser.add_argument(
        "--severities",
        nargs="*",
        default=None,
        help="Fault severities to include",
    )

    # revert
    revert_parser = subparsers.add_parser("revert", help="Revert fault injection")
    revert_parser.add_argument("--repo-path", required=True, help="Path to the repository")

    return parser


def main(argv=None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {
        "list": cmd_list,
        "inject": cmd_inject,
        "revert": cmd_revert,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
