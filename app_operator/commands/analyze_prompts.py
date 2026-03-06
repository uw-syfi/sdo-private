"""Analyze prompt performance from trajectory data.

This command analyzes trajectory files to compute metrics on prompt performance,
including success rates, iteration efficiency, and token costs.
"""

import json
import sys
from pathlib import Path

from tabulate import tabulate

from app_operator.dspy_integration import MetricsAggregator


def add_arguments(parser):
    """Add arguments for analyze-prompts command."""
    parser.add_argument(
        "--trajectories-dir",
        type=Path,
        help="Directory containing trajectory files (default: .sds/trajectories in current dir)",
    )
    parser.add_argument(
        "--phase",
        choices=["deployment", "monitoring", "script_generation", "exploration"],
        help="Filter by specific phase",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model name for cost calculation (e.g., 'claude-sonnet-4-5')",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--compare",
        type=str,
        help="Compare two trajectory directories (format: baseline_dir:optimized_dir)",
    )


def run_command(args) -> int:
    """Execute analyze-prompts command.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # Comparison mode
    if args.compare:
        return _compare_mode(args.compare, args.phase, args.model, args.format)

    # Determine trajectories directory
    trajectories_dir = args.trajectories_dir
    if trajectories_dir is None:
        trajectories_dir = Path.cwd() / ".sds" / "trajectories"

    if not trajectories_dir.exists():
        print(f"Error: Trajectories directory not found: {trajectories_dir}", file=sys.stderr)
        print("Run the operator first to generate trajectory data.", file=sys.stderr)
        return 1

    # Single directory analysis mode
    aggregator = MetricsAggregator(trajectories_dir)
    metrics = aggregator.aggregate_metrics(phase_filter=args.phase, model=args.model)

    if metrics.get("total_examples", 0) == 0:
        print("No trajectory examples found.", file=sys.stderr)
        if "error" in metrics:
            print(f"Error: {metrics['error']}", file=sys.stderr)
        return 1

    # Output results
    if args.format == "json":
        print(json.dumps(metrics, indent=2))
    else:
        _print_table_format(metrics, trajectories_dir)

    return 0


def _compare_mode(
    compare: str,
    phase: str | None,
    model: str | None,
    format_type: str,
) -> int:
    """Compare two trajectory directories."""
    try:
        baseline_str, optimized_str = compare.split(":")
        baseline_dir = Path(baseline_str)
        optimized_dir = Path(optimized_str)
    except ValueError:
        print("Error: --compare must be in format 'baseline_dir:optimized_dir'", file=sys.stderr)
        return 1

    if not baseline_dir.exists():
        print(f"Error: Baseline directory not found: {baseline_dir}", file=sys.stderr)
        return 1

    if not optimized_dir.exists():
        print(f"Error: Optimized directory not found: {optimized_dir}", file=sys.stderr)
        return 1

    # Load comparison
    aggregator = MetricsAggregator(baseline_dir)
    comparison = aggregator.compare_versions(baseline_dir, optimized_dir, phase, model)

    # Output results
    if format_type == "json":
        print(json.dumps(comparison, indent=2))
    else:
        _print_comparison_table(comparison)

    return 0


def _print_table_format(metrics: dict, trajectories_dir: Path):
    """Print metrics in human-readable table format."""
    print("\n" + "=" * 60)
    print(f"Trajectory Analysis: {trajectories_dir}")
    print("=" * 60 + "\n")

    overall = metrics.get("overall", {})

    # Summary table
    summary_data = [
        ["Total Examples", metrics.get("total_examples", 0)],
        ["Total Runs", metrics.get("total_runs", 0)],
        ["Success Rate", f"{overall.get('success_rate', 0) * 100:.2f}%"],
        ["Successful", overall.get("successful_count", 0)],
        ["Failed", overall.get("failed_count", 0)],
    ]

    print("Summary:")
    print(tabulate(summary_data, tablefmt="simple"))
    print()

    # Iterations table
    if "iterations" in overall:
        iter_data = overall["iterations"]
        iter_table = [
            ["Average Iterations", iter_data.get("avg", 0)],
            ["Median Iterations", iter_data.get("median", 0)],
            ["Min Iterations", iter_data.get("min", 0)],
            ["Max Iterations", iter_data.get("max", 0)],
        ]

        if "by_success" in iter_data:
            by_success = iter_data["by_success"]
            iter_table.extend(
                [
                    ["Avg (Successful)", by_success.get("successful_avg", 0)],
                    ["Avg (Failed)", by_success.get("failed_avg", 0)],
                ]
            )

        print("Iterations:")
        print(tabulate(iter_table, tablefmt="simple"))
        print()

    # Duration table
    if "duration" in overall:
        dur_data = overall["duration"]
        dur_table = [
            ["Avg Duration", f"{dur_data.get('avg_seconds', 0):.2f}s"],
            ["Total Duration", f"{dur_data.get('total_hours', 0):.2f}h"],
        ]

        print("Duration:")
        print(tabulate(dur_table, tablefmt="simple"))
        print()

    # Token metrics
    if "tokens" in overall and overall["tokens"].get("available"):
        token_data = overall["tokens"]
        token_table = [
            ["Total Input Tokens", f"{token_data.get('total_input', 0):,}"],
            ["Total Output Tokens", f"{token_data.get('total_output', 0):,}"],
            ["Total Tokens", f"{token_data.get('total', 0):,}"],
            ["Avg Input Tokens", f"{token_data.get('avg_input', 0):.2f}"],
            ["Avg Output Tokens", f"{token_data.get('avg_output', 0):.2f}"],
        ]

        if "cost_usd" in token_data and "total" in token_data["cost_usd"]:
            cost_data = token_data["cost_usd"]
            token_table.extend(
                [
                    ["", ""],  # Separator
                    ["Total Cost", f"${cost_data.get('total', 0):.4f}"],
                    ["Avg Cost per Run", f"${cost_data.get('avg_per_run', 0):.4f}"],
                    ["Model", cost_data.get("model", "unknown")],
                ]
            )

        print("Token Usage:")
        print(tabulate(token_table, tablefmt="simple"))
        print()

    # By-phase breakdown
    if "by_phase" in metrics and len(metrics["by_phase"]) > 1:
        print("By Phase:")
        phase_rows = []
        for phase_name, phase_metrics in metrics["by_phase"].items():
            phase_rows.append(
                [
                    phase_name.capitalize(),
                    phase_metrics.get("count", 0),
                    f"{phase_metrics.get('success_rate', 0) * 100:.2f}%",
                    f"{phase_metrics.get('iterations', {}).get('avg', 0):.2f}",
                ]
            )

        print(
            tabulate(
                phase_rows,
                headers=["Phase", "Count", "Success Rate", "Avg Iterations"],
                tablefmt="simple",
            )
        )
        print()


def _format_pct_improvement(value) -> str:
    """Format a percentage improvement value, handling None as N/A."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _print_comparison_table(comparison: dict):
    """Print comparison results in table format."""
    print("\n" + "=" * 60)
    print("Baseline vs Optimized Comparison")
    print("=" * 60 + "\n")

    baseline = comparison["baseline"]["overall"]
    optimized = comparison["optimized"]["overall"]
    improvements = comparison["improvements"]

    # Main comparison table
    comp_table = [
        [
            "Metric",
            "Baseline",
            "Optimized",
            "Improvement",
        ],
        [
            "Success Rate",
            f"{baseline.get('success_rate', 0) * 100:.2f}%",
            f"{optimized.get('success_rate', 0) * 100:.2f}%",
            _format_pct_improvement(improvements.get("success_rate_improvement")),
        ],
        [
            "Avg Iterations",
            f"{baseline.get('iterations', {}).get('avg', 0):.2f}",
            f"{optimized.get('iterations', {}).get('avg', 0):.2f}",
            _format_pct_improvement(improvements.get("iteration_reduction_pct")),
        ],
    ]

    # Add token usage comparison if available
    baseline_tokens = baseline.get("tokens", {})
    optimized_tokens = optimized.get("tokens", {})
    if baseline_tokens.get("available") and optimized_tokens.get("available"):
        token_label = "Total Tokens (est.)" if baseline_tokens.get("estimated") else "Total Tokens"
        comp_table.append(
            [
                token_label,
                f"{baseline_tokens.get('total', 0):,}",
                f"{optimized_tokens.get('total', 0):,}",
                _format_pct_improvement(improvements.get("token_reduction_pct")),
            ]
        )

    # Add cost comparison if available
    if "cost_reduction_pct" in improvements:
        baseline_cost = baseline.get("tokens", {}).get("cost_usd", {}).get("total", 0)
        optimized_cost = optimized.get("tokens", {}).get("cost_usd", {}).get("total", 0)

        comp_table.append(
            [
                "Total Cost",
                f"${baseline_cost:.4f}",
                f"${optimized_cost:.4f}",
                _format_pct_improvement(improvements.get("cost_reduction_pct")),
            ]
        )

        if "cost_savings_usd" in improvements:
            comp_table.append(
                [
                    "Cost Savings",
                    "",
                    "",
                    f"${improvements['cost_savings_usd']:.4f}",
                ]
            )

    # Add fallback rate comparison if available
    if "fallback_rate_reduction_pct" in improvements:
        baseline_fr = baseline.get("fallback_rate", 0)
        optimized_fr = optimized.get("fallback_rate", 0)
        comp_table.append(
            [
                "Fallback Rate",
                f"{baseline_fr * 100:.2f}%",
                f"{optimized_fr * 100:.2f}%",
                _format_pct_improvement(improvements.get("fallback_rate_reduction_pct")),
            ]
        )

    print(tabulate(comp_table, headers="firstrow", tablefmt="grid"))
    print()

    # Summary
    print("Summary:")
    sr_improvement = improvements.get("success_rate_improvement") or 0
    if sr_improvement > 0:
        print("  ✓ Success rate improved")
    elif sr_improvement < 0:
        print("  ✗ Success rate degraded")

    iter_reduction = improvements.get("iteration_reduction_pct") or 0
    if iter_reduction > 0:
        print("  ✓ Iteration efficiency improved")
    elif iter_reduction < 0:
        print("  ✗ More iterations needed")

    token_reduction = improvements.get("token_reduction_pct") or 0
    if token_reduction > 0:
        print("  ✓ Token usage reduced")
    elif token_reduction < 0:
        print("  ✗ Token usage increased")

    cost_reduction = improvements.get("cost_reduction_pct") or 0
    if cost_reduction > 0:
        print("  ✓ Token costs reduced")
    elif cost_reduction < 0:
        print("  ✗ Token costs increased")

    if "fallback_rate_reduction_pct" in improvements:
        fb_reduction = improvements.get("fallback_rate_reduction_pct")
        if fb_reduction is None:
            print("  ✗ Fallback rate increased from zero")
        elif fb_reduction > 0:
            print("  ✓ Fallback rate reduced")
        elif fb_reduction < 0:
            print("  ✗ Fallback rate increased")

    print()

    # Per-phase breakdown
    by_phase = comparison.get("by_phase", {})
    if by_phase:
        print("Per-Phase Comparison:")
        phase_rows = []
        for phase_name, phase_data in by_phase.items():
            b = phase_data["baseline"]
            o = phase_data["optimized"]
            ph_impr = phase_data["improvements"]
            phase_rows.append(
                [
                    phase_name.capitalize(),
                    f"{b.get('success_rate', 0) * 100:.2f}%",
                    f"{o.get('success_rate', 0) * 100:.2f}%",
                    _format_pct_improvement(ph_impr.get("success_rate_improvement")),
                    f"{b.get('iterations', {}).get('avg', 0):.2f}",
                    f"{o.get('iterations', {}).get('avg', 0):.2f}",
                    _format_pct_improvement(ph_impr.get("iteration_reduction_pct")),
                ]
            )

        print(
            tabulate(
                phase_rows,
                headers=[
                    "Phase",
                    "Success (B)",
                    "Success (O)",
                    "Success Δ",
                    "Iters (B)",
                    "Iters (O)",
                    "Iters Δ",
                ],
                tablefmt="grid",
            )
        )
        print()
