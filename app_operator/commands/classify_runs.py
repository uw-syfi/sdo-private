"""CLI command: classify experiment runs into quality categories."""

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app_operator.run_classifier import classify_run, classify_workdir


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        help="Path to a workdir (classifies all runs) or a single run directory",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output results as JSON instead of a table",
    )
    parser.add_argument(
        "--label",
        choices=[
            "true_success",
            "recovered_success",
            "false_positive",
            "telemetry_inconsistent",
            "true_failure",
        ],
        help="Filter to only show runs with this label",
    )


def run_command(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()

    if not path.exists():
        Console(stderr=True).print(f"[red]Path not found: {path}[/red]")
        return 1

    # Decide if this is a workdir or a single run
    if (path / ".sds").exists():
        results = [classify_run(path)]
    else:
        results = classify_workdir(path)

    if not results:
        Console(stderr=True).print("[yellow]No classifiable runs found.[/yellow]")
        return 0

    if args.label:
        results = [r for r in results if r.label == args.label]

    if args.output_json:
        entries = [
            {
                "run_dir": r.run_dir,
                "label": r.label,
                "trajectory_status": r.trajectory_status,
                "trajectory_attempts": r.trajectory_attempts,
                "log_deploy_attempts": r.log_deploy_attempts,
                "log_health_checks": r.log_health_checks,
                "log_health_rechecks": r.log_health_rechecks,
                "final_health_exit_code": r.final_health_exit_code,
                "health_contradictions": r.health_contradictions,
                "monitor_concerns": r.monitor_concerns,
                "reasons": r.reasons,
            }
            for r in results
        ]
        print(json.dumps(entries, indent=2))
        return 0

    console = Console()

    # Summary counts
    counts: dict[str, int] = {}
    for r in results:
        counts[r.label] = counts.get(r.label, 0) + 1

    label_colors = {
        "true_success": "green",
        "recovered_success": "yellow",
        "false_positive": "red",
        "telemetry_inconsistent": "magenta",
        "true_failure": "dim",
    }

    console.print(f"\n[bold]Run Classification Summary[/bold] ({len(results)} runs)")
    for label, count in sorted(counts.items()):
        color = label_colors.get(label, "white")
        console.print(f"  [{color}]{label}[/{color}]: {count}")
    console.print()

    table = Table(show_lines=True)
    table.add_column("Run", style="cyan", no_wrap=True)
    table.add_column("Label")
    table.add_column("Traj Status")
    table.add_column("Deploy\nAttempts")
    table.add_column("Health\nChecks")
    table.add_column("Final HC\nExit")
    table.add_column("Reasons", max_width=60)

    for r in results:
        color = label_colors.get(r.label, "white")
        label_str = f"[{color}]{r.label}[/{color}]"
        traj_status = r.trajectory_status or "N/A"
        deploy_str = f"{r.log_deploy_attempts}"
        if r.trajectory_attempts != r.log_deploy_attempts:
            deploy_str += f" (traj: {r.trajectory_attempts})"
        hc_str = f"{r.log_health_checks}"
        if r.log_health_rechecks:
            hc_str += f"+{r.log_health_rechecks}r"
        exit_str = str(r.final_health_exit_code) if r.final_health_exit_code is not None else "N/A"
        reasons = "\n".join(r.reasons) if r.reasons else ""

        table.add_row(r.run_dir, label_str, traj_status, deploy_str, hc_str, exit_str, reasons)

    console.print(table)
    return 0
