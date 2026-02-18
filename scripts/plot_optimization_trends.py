#!/usr/bin/env python3
"""Plot the 4 core DSPy optimization metrics as trends.

The 4 metrics we care about:
1. Deployment Success Rate (50% weight)
2. Iteration Efficiency - fewer deployment iterations (25% weight)
3. Token Efficiency - lower token usage (15% weight)
4. Health Check Quality (10% weight)

Usage:
    python scripts/plot_optimization_trends.py --work-dir opt1
    python scripts/plot_optimization_trends.py --work-dir opt1 --output trends.png
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict
import sys

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError:
    print("Error: matplotlib not installed. Run: uv pip install matplotlib")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("Error: numpy not installed. Run: uv pip install numpy")
    sys.exit(1)


def collect_metrics(work_dir: Path) -> Dict[str, Any]:
    """Collect the 4 core metrics from trajectory files."""
    metrics_by_iteration = defaultdict(lambda: {
        'train': {'runs': []},
        'val': {'runs': []}
    })

    # Find all experiment directories
    exp_dirs = sorted([d for d in work_dir.iterdir() if d.is_dir() and not d.name.startswith('.')])

    for exp_dir in exp_dirs:
        # Parse: {app}_iter{N}_{train|val}
        parts = exp_dir.name.split("_")

        # Find iteration number
        iter_part = [p for p in parts if p.startswith("iter")]
        if not iter_part:
            continue
        iteration = int(iter_part[0].replace("iter", ""))

        # Find phase
        phase = parts[-1] if parts[-1] in ["train", "val"] else None
        if not phase:
            continue

        # Read trajectories
        traj_dir = exp_dir / ".sds" / "trajectories"
        if not traj_dir.exists():
            continue

        for traj_file in traj_dir.glob("*.json"):
            try:
                with open(traj_file) as f:
                    traj = json.load(f)

                run_metrics = extract_four_metrics(traj)
                metrics_by_iteration[iteration][phase]['runs'].append(run_metrics)

            except Exception as e:
                print(f"Warning: Failed to parse {traj_file.name}: {e}")

    return aggregate_metrics(metrics_by_iteration)


def extract_four_metrics(traj: Dict[str, Any]) -> Dict[str, float]:
    """Extract the 4 core metrics from a single trajectory."""

    # 1. Deployment Success (binary: 0 or 1)
    success = 1.0 if traj.get("success", False) else 0.0

    # 2. Iteration Efficiency (deployment iterations to success)
    deployment_iters = 0
    phases = traj.get("phases", {})
    if "deployment" in phases:
        deployment_iters = len(phases["deployment"].get("conversations", []))

    # Normalize: fewer iterations is better, scale inversely
    # Max expected iterations ~ 20, so efficiency = 1 - (iters / 20)
    iteration_efficiency = max(0.0, 1.0 - (deployment_iters / 20.0)) if success else 0.0

    # 3. Token Efficiency (total tokens used)
    total_tokens = 0
    for call in traj.get("calls", []):
        usage = call.get("usage", {})
        total_tokens += usage.get("total_tokens", 0)

    # Normalize: fewer tokens is better, assume baseline ~15k tokens
    # efficiency = 1 - (tokens / 30000), capped at 0-1
    token_efficiency = max(0.0, min(1.0, 1.0 - (total_tokens / 30000.0))) if success else 0.0

    # 4. Health Check Quality (0 or 1 based on script quality)
    health_check_quality = 0.0
    if "deployment" in phases:
        for conv in phases["deployment"].get("conversations", []):
            for msg in conv.get("messages", []):
                content = msg.get("content", "")
                if "health_check.sh" in content:
                    # Quality checks:
                    # - Length > 500 chars
                    # - Has actual commands (curl, nc, docker, etc.)
                    # - Not trivial (not just "exit 0")
                    has_length = len(content) > 500
                    has_commands = any(cmd in content for cmd in
                                     ["curl", "nc ", "docker", "redis-cli", "mongo", "psql"])
                    not_trivial = "exit 0" not in content or len(content) > 200

                    if has_length and has_commands and not_trivial:
                        health_check_quality = 1.0
                    break

    return {
        'success': success,
        'iteration_efficiency': iteration_efficiency,
        'token_efficiency': token_efficiency,
        'health_check_quality': health_check_quality,
        'raw_tokens': total_tokens,
        'raw_iterations': deployment_iters,
    }


def aggregate_metrics(metrics_by_iteration: Dict) -> Dict[str, Any]:
    """Aggregate metrics across runs for each iteration."""
    result = {
        'iterations': [],
        'train': defaultdict(list),
        'val': defaultdict(list),
    }

    for iteration in sorted(metrics_by_iteration.keys()):
        result['iterations'].append(iteration)

        for phase in ['train', 'val']:
            runs = metrics_by_iteration[iteration][phase]['runs']
            if not runs:
                # Append NaN for missing data
                for metric in ['success', 'iteration_efficiency', 'token_efficiency', 'health_check_quality']:
                    result[phase][metric].append(np.nan)
                result[phase]['num_runs'].append(0)
                continue

            # Average each metric across runs
            result[phase]['success'].append(np.mean([r['success'] for r in runs]))
            result[phase]['iteration_efficiency'].append(
                np.mean([r['iteration_efficiency'] for r in runs if r['success']])
                if any(r['success'] for r in runs) else 0.0
            )
            result[phase]['token_efficiency'].append(
                np.mean([r['token_efficiency'] for r in runs if r['success']])
                if any(r['success'] for r in runs) else 0.0
            )
            result[phase]['health_check_quality'].append(
                np.mean([r['health_check_quality'] for r in runs if r['success']])
                if any(r['success'] for r in runs) else 0.0
            )
            result[phase]['num_runs'].append(len(runs))

    return result


def plot_four_metrics(metrics: Dict[str, Any], output_path: Path = None):
    """Create a 2x2 grid showing the 4 core metrics."""

    if not metrics['iterations']:
        print("No data to plot!")
        return

    iterations = metrics['iterations']

    # Create figure with 2x2 subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('DSPy Optimization Metrics: Training vs Validation',
                 fontsize=16, fontweight='bold', y=0.995)

    # Colors
    train_color = '#2E86AB'  # Blue
    val_color = '#A23B72'    # Purple

    # Version labels for x-axis
    version_labels = ['Seeds'] + [f'v{i}' for i in range(1, len(iterations))]

    # === 1. Deployment Success Rate (50% weight) ===
    ax1 = axes[0, 0]
    plot_metric(ax1, iterations,
                metrics['train']['success'], metrics['val']['success'],
                'Deployment Success Rate', 'Success Rate',
                train_color, val_color, version_labels,
                percentage=True, weight='50%')

    # === 2. Iteration Efficiency (25% weight) ===
    ax2 = axes[0, 1]
    plot_metric(ax2, iterations,
                metrics['train']['iteration_efficiency'],
                metrics['val']['iteration_efficiency'],
                'Iteration Efficiency', 'Efficiency Score',
                train_color, val_color, version_labels,
                percentage=False, weight='25%')

    # === 3. Token Efficiency (15% weight) ===
    ax3 = axes[1, 0]
    plot_metric(ax3, iterations,
                metrics['train']['token_efficiency'],
                metrics['val']['token_efficiency'],
                'Token Efficiency', 'Efficiency Score',
                train_color, val_color, version_labels,
                percentage=False, weight='15%')

    # === 4. Health Check Quality (10% weight) ===
    ax4 = axes[1, 1]
    plot_metric(ax4, iterations,
                metrics['train']['health_check_quality'],
                metrics['val']['health_check_quality'],
                'Health Check Quality', 'Quality Score',
                train_color, val_color, version_labels,
                percentage=False, weight='10%')

    plt.tight_layout()

    # Save or show
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        print(f"✓ Saved plot to: {output_path}")
    else:
        plt.show()


def plot_metric(ax, iterations, train_data, val_data, title, ylabel,
                train_color, val_color, version_labels, percentage=False, weight=''):
    """Plot a single metric with training and validation lines."""

    # Filter out NaN values for plotting
    train_valid = [(i, v) for i, v in zip(iterations, train_data) if not np.isnan(v)]
    val_valid = [(i, v) for i, v in zip(iterations, val_data) if not np.isnan(v)]

    # Plot training
    if train_valid:
        train_iters, train_vals = zip(*train_valid)
        if percentage:
            train_vals = [v * 100 for v in train_vals]
        ax.plot(train_iters, train_vals, marker='o', linewidth=2.5, markersize=9,
                label='Training', color=train_color, alpha=0.9)

    # Plot validation
    if val_valid:
        val_iters, val_vals = zip(*val_valid)
        if percentage:
            val_vals = [v * 100 for v in val_vals]
        ax.plot(val_iters, val_vals, marker='s', linewidth=2.5, markersize=9,
                label='Validation', color=val_color, alpha=0.9)

    # Formatting
    ax.set_xlabel('Optimization Iteration', fontsize=11, fontweight='bold')
    ax.set_ylabel(ylabel, fontsize=11, fontweight='bold')
    ax.set_title(f'{title} (Weight: {weight})', fontsize=12, fontweight='bold', pad=10)
    ax.legend(loc='best', framealpha=0.9)
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_xticks(iterations)
    ax.set_xticklabels(version_labels, fontsize=9)

    # Set y-axis limits
    if percentage:
        ax.set_ylim([0, 105])
    else:
        ax.set_ylim([0, 1.05])

    # Add trend annotation if we have multiple points
    if len(train_valid) > 1:
        initial = train_valid[0][1]
        final = train_valid[-1][1]
        if percentage:
            change = (final - initial) * 100
            initial *= 100
            final *= 100
        else:
            change = final - initial

        trend = "↑" if change > 0 else "↓" if change < 0 else "→"
        color = 'green' if change > 0 else 'red' if change < 0 else 'gray'

        ax.text(0.98, 0.02, f'{trend} {abs(change):.1f}{"pp" if percentage else ""}',
                transform=ax.transAxes, fontsize=10, fontweight='bold',
                ha='right', va='bottom', color=color,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))


def print_metrics_table(metrics: Dict[str, Any]):
    """Print a formatted table of metrics."""
    print("\n" + "=" * 90)
    print("OPTIMIZATION METRICS SUMMARY")
    print("=" * 90)

    iterations = metrics['iterations']
    version_labels = ['Seeds'] + [f'v{i}' for i in range(1, len(iterations))]

    # Header
    print(f"\n{'Iter':<6} {'Version':<8} {'Success':>10} {'IterEff':>10} {'TokenEff':>10} {'HCQual':>10} {'Runs':>6}")
    print("-" * 90)

    # Training data
    print("TRAINING:")
    for i, iter_num in enumerate(iterations):
        if metrics['train']['num_runs'][i] == 0:
            continue
        print(f"  {iter_num:<4} {version_labels[i]:<8} "
              f"{metrics['train']['success'][i]*100:>9.1f}% "
              f"{metrics['train']['iteration_efficiency'][i]:>10.3f} "
              f"{metrics['train']['token_efficiency'][i]:>10.3f} "
              f"{metrics['train']['health_check_quality'][i]:>10.3f} "
              f"{metrics['train']['num_runs'][i]:>6}")

    # Validation data
    if any(metrics['val']['num_runs']):
        print("\nVALIDATION:")
        for i, iter_num in enumerate(iterations):
            if metrics['val']['num_runs'][i] == 0:
                continue
            print(f"  {iter_num:<4} {version_labels[i]:<8} "
                  f"{metrics['val']['success'][i]*100:>9.1f}% "
                  f"{metrics['val']['iteration_efficiency'][i]:>10.3f} "
                  f"{metrics['val']['token_efficiency'][i]:>10.3f} "
                  f"{metrics['val']['health_check_quality'][i]:>10.3f} "
                  f"{metrics['val']['num_runs'][i]:>6}")

    # Calculate improvements
    if len(iterations) > 1 and metrics['train']['num_runs'][0] > 0 and metrics['train']['num_runs'][-1] > 0:
        print("\n" + "-" * 90)
        print("TRAINING IMPROVEMENT (Final vs Initial):")

        initial_success = metrics['train']['success'][0] * 100
        final_success = metrics['train']['success'][-1] * 100

        print(f"  Success Rate:        {initial_success:.1f}% → {final_success:.1f}% "
              f"({final_success - initial_success:+.1f}pp)")
        print(f"  Iteration Efficiency: {metrics['train']['iteration_efficiency'][0]:.3f} → "
              f"{metrics['train']['iteration_efficiency'][-1]:.3f} "
              f"({metrics['train']['iteration_efficiency'][-1] - metrics['train']['iteration_efficiency'][0]:+.3f})")
        print(f"  Token Efficiency:     {metrics['train']['token_efficiency'][0]:.3f} → "
              f"{metrics['train']['token_efficiency'][-1]:.3f} "
              f"({metrics['train']['token_efficiency'][-1] - metrics['train']['token_efficiency'][0]:+.3f})")
        print(f"  Health Check Quality: {metrics['train']['health_check_quality'][0]:.3f} → "
              f"{metrics['train']['health_check_quality'][-1]:.3f} "
              f"({metrics['train']['health_check_quality'][-1] - metrics['train']['health_check_quality'][0]:+.3f})")

    print("\n" + "=" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Plot the 4 core DSPy optimization metrics as trends'
    )
    parser.add_argument('--work-dir', default='e2e_optimization',
                       help='Working directory with experiment results')
    parser.add_argument('--output', help='Output PNG file path (default: show interactive plot)')
    parser.add_argument('--no-plot', action='store_true',
                       help='Print table only, skip plotting')

    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    if not work_dir.exists():
        print(f"Error: Work directory not found: {work_dir}")
        return 1

    print(f"Collecting metrics from: {work_dir}")
    metrics = collect_metrics(work_dir)

    if not metrics['iterations']:
        print("No experiment data found!")
        return 1

    # Print table
    print_metrics_table(metrics)

    # Generate plot
    if not args.no_plot:
        output_path = Path(args.output) if args.output else None
        plot_four_metrics(metrics, output_path)

    return 0


if __name__ == '__main__':
    sys.exit(main())
