#!/usr/bin/env python3
"""Visualize metrics from e2e-optimize experiment results with improved units.

Improvements over v1:
- Token usage shown in K (thousands) or M (millions) with proper units
- Agent calls labeled as "LLM Invocations per Run"
- Better tooltips and explanations
- Clearer axis labels with units

Usage:
    python scripts/visualize_e2e_results_v2.py --work-dir opt1
    python scripts/visualize_e2e_results_v2.py --work-dir opt1 --output results_v2.png
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Any, Optional
from collections import defaultdict
import sys

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.gridspec import GridSpec
except ImportError:
    print("Error: matplotlib not installed. Run: uv pip install matplotlib")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("Error: numpy not installed. Run: uv pip install numpy")
    sys.exit(1)


class MetricsCollector:
    """Collect metrics from trajectory files."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.metrics = defaultdict(lambda: defaultdict(list))

    def collect(self) -> Dict[str, Any]:
        """Collect all metrics from experiment directories."""
        # Find all experiment directories
        exp_dirs = sorted([d for d in self.work_dir.iterdir() if d.is_dir()])

        for exp_dir in exp_dirs:
            # Parse directory name: {app}_iter{N}_{train|val}
            parts = exp_dir.name.split("_")
            if len(parts) < 3:
                continue

            # Extract iteration number
            iter_part = [p for p in parts if p.startswith("iter")]
            if not iter_part:
                continue
            iteration = int(iter_part[0].replace("iter", ""))

            # Extract phase (train/val)
            phase = parts[-1] if parts[-1] in ["train", "val"] else "unknown"

            # Extract app name (everything before iter)
            app_name = "_".join(parts[:parts.index(iter_part[0])])

            # Read trajectory files
            traj_dir = exp_dir / ".sds" / "trajectories"
            if not traj_dir.exists():
                print(f"Warning: No trajectories found in {exp_dir.name}")
                continue

            for traj_file in traj_dir.glob("*.json"):
                try:
                    with open(traj_file) as f:
                        traj_data = json.load(f)

                    metrics = self._extract_metrics(traj_data, traj_dir)
                    metrics["app"] = app_name
                    metrics["phase"] = phase
                    metrics["iteration"] = iteration
                    metrics["exp_dir"] = exp_dir.name

                    # Store metrics
                    key = f"iter{iteration}_{phase}"
                    self.metrics[key]["runs"].append(metrics)

                except Exception as e:
                    print(f"Warning: Failed to parse {traj_file}: {e}")

        return self._aggregate_metrics()

    def _extract_metrics(
        self, traj_data: Dict[str, Any], traj_dir: Path
    ) -> Dict[str, Any]:
        """Extract relevant metrics from a trajectory."""
        # Extract success from metadata.status
        metadata = traj_data.get("metadata", {})
        status = metadata.get("status", "")
        success = status == "completed"

        # Get prompt version from first call that has it
        prompt_version = "unknown"
        for call in traj_data.get("calls", []):
            if "prompt_version" in call:
                prompt_version = call["prompt_version"]
                break

        metrics = {
            "success": success,
            "deployment_iterations": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "num_agent_calls": 0,
            "health_check_quality": 0,
            "prompt_version": prompt_version,
        }

        # Count deployment iterations from deployment array
        deployment = traj_data.get("deployment", [])
        if isinstance(deployment, list):
            metrics["deployment_iterations"] = len(deployment)
        elif isinstance(deployment, dict):
            metrics["deployment_iterations"] = len(deployment)

        # Count agent calls (all phases)
        metrics["num_agent_calls"] = len(traj_data.get("calls", []))

        # Load token usage from gemini_sessions files
        gemini_sessions = traj_data.get("gemini_sessions", [])
        if isinstance(gemini_sessions, list):
            for session_path in gemini_sessions:
                if session_path.startswith("trajectories/"):
                    session_path = session_path[len("trajectories/") :]
                session_file = traj_dir / session_path
                if session_file.exists():
                    try:
                        with open(session_file) as f:
                            session_data = json.load(f)
                        for msg in session_data.get("messages", []):
                            tokens = msg.get("tokens", {})
                            if tokens:
                                metrics["total_tokens"] += tokens.get("total", 0)
                                metrics["prompt_tokens"] += tokens.get("input", 0)
                                metrics["completion_tokens"] += tokens.get("output", 0)
                    except Exception:
                        pass

        return metrics

    def _aggregate_metrics(self) -> Dict[str, Any]:
        """Aggregate metrics by iteration and phase."""
        aggregated = {
            "iterations": [],
            "train": defaultdict(list),
            "val": defaultdict(list),
            "raw": self.metrics,
        }

        for key, data in sorted(self.metrics.items()):
            iter_num = int(key.split("_")[0].replace("iter", ""))
            phase = key.split("_")[1]

            if iter_num not in aggregated["iterations"]:
                aggregated["iterations"].append(iter_num)

            runs = data.get("runs", [])
            if not runs:
                continue

            # Aggregate metrics
            success_rate = sum(r["success"] for r in runs) / len(runs)
            avg_iterations = np.mean(
                [r["deployment_iterations"] for r in runs if r["success"]]
                or [0]
            )
            avg_tokens = np.mean([r["total_tokens"] for r in runs])
            avg_calls = np.mean([r["num_agent_calls"] for r in runs])

            target = aggregated[phase]
            target["success_rate"].append(success_rate)
            target["avg_iterations"].append(avg_iterations)
            target["avg_tokens"].append(avg_tokens)
            target["avg_calls"].append(avg_calls)
            target["num_runs"].append(len(runs))

        return aggregated


def format_tokens(tokens):
    """Format token count with appropriate unit (K or M)."""
    if tokens >= 1_000_000:
        return f"{tokens/1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens/1_000:.1f}K"
    else:
        return f"{int(tokens)}"


def plot_metrics(metrics: Dict[str, Any], output_path: Optional[Path] = None):
    """Create comprehensive visualization with proper units."""
    iterations = metrics["iterations"]
    if not iterations:
        print("No data to plot!")
        return

    # Create figure with subplots
    fig = plt.figure(figsize=(18, 13))
    gs = GridSpec(3, 2, figure=fig, hspace=0.35, wspace=0.3)

    # Color scheme
    train_color = "#2E86AB"  # Blue
    val_color = "#A23B72"  # Purple

    # 1. Success Rate Over Iterations
    ax1 = fig.add_subplot(gs[0, 0])
    if metrics["train"]["success_rate"]:
        ax1.plot(
            iterations,
            [r * 100 for r in metrics["train"]["success_rate"]],
            marker="o",
            linewidth=2.5,
            markersize=10,
            label="Training",
            color=train_color,
        )
    if metrics["val"]["success_rate"]:
        ax1.plot(
            iterations,
            [r * 100 for r in metrics["val"]["success_rate"]],
            marker="s",
            linewidth=2.5,
            markersize=10,
            label="Validation",
            color=val_color,
        )
    ax1.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax1.set_ylabel("Success Rate (%)", fontsize=13, fontweight="bold")
    ax1.set_title("Deployment Success Rate", fontsize=15, fontweight="bold", pad=15)
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(iterations)
    ax1.set_ylim([0, 105])

    # Add version labels
    for i, iter_num in enumerate(iterations):
        version = "Seed Prompts" if iter_num == 1 else f"Optimized v{iter_num-1}"
        ax1.text(
            iter_num,
            5,
            version,
            ha="center",
            fontsize=9,
            style="italic",
            color="gray",
        )

    # 2. Token Usage Over Iterations with UNITS
    ax2 = fig.add_subplot(gs[0, 1])
    if metrics["train"]["avg_tokens"]:
        # Convert to millions for readability
        train_tokens_m = [t / 1_000_000 for t in metrics["train"]["avg_tokens"]]
        ax2.plot(
            iterations,
            train_tokens_m,
            marker="o",
            linewidth=2.5,
            markersize=10,
            label="Training",
            color=train_color,
        )
        # Add value labels with fixed offset
        y_range = max(train_tokens_m) - min(train_tokens_m) if len(train_tokens_m) > 1 else max(train_tokens_m)
        offset = y_range * 0.08 if y_range > 0 else 0.1  # 8% of range
        for i, (x, y) in enumerate(zip(iterations, train_tokens_m)):
            # Show values under 1M in thousands (K), otherwise in millions (M)
            label = f"{y*1000:.0f}K" if y < 1.0 else f"{y:.2f}M"
            ax2.text(
                x, y + offset, label,
                ha="center", va="bottom", fontsize=9, color=train_color, fontweight="bold"
            )

    if metrics["val"]["avg_tokens"]:
        val_tokens_m = [t / 1_000_000 for t in metrics["val"]["avg_tokens"]]
        ax2.plot(
            iterations,
            val_tokens_m,
            marker="s",
            linewidth=2.5,
            markersize=10,
            label="Validation",
            color=val_color,
        )
        # Add value labels with fixed offset
        y_range = max(val_tokens_m) - min(val_tokens_m) if len(val_tokens_m) > 1 else max(val_tokens_m)
        offset = y_range * 0.08 if y_range > 0 else 0.1
        for i, (x, y) in enumerate(zip(iterations, val_tokens_m)):
            # Show values under 1M in thousands (K), otherwise in millions (M)
            label = f"{y*1000:.0f}K" if y < 1.0 else f"{y:.2f}M"
            ax2.text(
                x, y + offset, label,
                ha="center", va="bottom", fontsize=9, color=val_color, fontweight="bold"
            )

    ax2.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax2.set_ylabel("Average Tokens per Run (millions)", fontsize=13, fontweight="bold")
    ax2.set_title("Token Usage Efficiency", fontsize=15, fontweight="bold", pad=15)
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(iterations)

    # 3. Deployment Iterations to Success
    ax3 = fig.add_subplot(gs[1, 0])
    if metrics["train"]["avg_iterations"]:
        ax3.plot(
            iterations,
            metrics["train"]["avg_iterations"],
            marker="o",
            linewidth=2.5,
            markersize=10,
            label="Training",
            color=train_color,
        )
        # Add value labels with fixed offset
        y_vals = metrics["train"]["avg_iterations"]
        y_range = max(y_vals) - min(y_vals) if len(y_vals) > 1 else max(y_vals)
        offset = max(y_range * 0.1, 0.2)  # At least 0.2 offset
        for x, y in zip(iterations, y_vals):
            ax3.text(
                x, y + offset, f"{y:.1f}",
                ha="center", va="bottom", fontsize=9, color=train_color, fontweight="bold"
            )

    if metrics["val"]["avg_iterations"]:
        ax3.plot(
            iterations,
            metrics["val"]["avg_iterations"],
            marker="s",
            linewidth=2.5,
            markersize=10,
            label="Validation",
            color=val_color,
        )
        # Add value labels with fixed offset
        y_vals = metrics["val"]["avg_iterations"]
        y_range = max(y_vals) - min(y_vals) if len(y_vals) > 1 else max(y_vals)
        offset = max(y_range * 0.1, 0.2)
        for x, y in zip(iterations, y_vals):
            ax3.text(
                x, y + offset, f"{y:.1f}",
                ha="center", va="bottom", fontsize=9, color=val_color, fontweight="bold"
            )

    ax3.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax3.set_ylabel("Avg Fix-Deploy Iterations", fontsize=13, fontweight="bold")
    ax3.set_title(
        "Deployment Efficiency (Lower = Better)", fontsize=15, fontweight="bold", pad=15
    )
    ax3.legend(fontsize=11)
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(iterations)

    # 4. LLM Invocations per Run
    ax4 = fig.add_subplot(gs[1, 1])
    if metrics["train"]["avg_calls"]:
        ax4.plot(
            iterations,
            metrics["train"]["avg_calls"],
            marker="o",
            linewidth=2.5,
            markersize=10,
            label="Training",
            color=train_color,
        )
        # Add value labels with fixed offset
        y_vals = metrics["train"]["avg_calls"]
        y_range = max(y_vals) - min(y_vals) if len(y_vals) > 1 else max(y_vals)
        offset = max(y_range * 0.1, 0.3)  # At least 0.3 offset
        for x, y in zip(iterations, y_vals):
            ax4.text(
                x, y + offset, f"{y:.1f}",
                ha="center", va="bottom", fontsize=9, color=train_color, fontweight="bold"
            )

    if metrics["val"]["avg_calls"]:
        ax4.plot(
            iterations,
            metrics["val"]["avg_calls"],
            marker="s",
            linewidth=2.5,
            markersize=10,
            label="Validation",
            color=val_color,
        )
        # Add value labels with fixed offset
        y_vals = metrics["val"]["avg_calls"]
        y_range = max(y_vals) - min(y_vals) if len(y_vals) > 1 else max(y_vals)
        offset = max(y_range * 0.1, 0.3)
        for x, y in zip(iterations, y_vals):
            ax4.text(
                x, y + offset, f"{y:.1f}",
                ha="center", va="bottom", fontsize=9, color=val_color, fontweight="bold"
            )

    ax4.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax4.set_ylabel("LLM Invocations per Run", fontsize=13, fontweight="bold")
    ax4.set_title(
        "Agent Call Frequency (Lower = Faster Resolution)",
        fontsize=15,
        fontweight="bold",
        pad=15
    )
    ax4.legend(fontsize=11)
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(iterations)

    # Add explanatory note
    ax4.text(
        0.5, -0.15,
        "Note: Each call = 1 LLM invocation (code analysis, script gen, error fix, or monitoring)",
        transform=ax4.transAxes,
        ha="center",
        fontsize=9,
        style="italic",
        color="gray"
    )

    # 5. Run Count Distribution
    ax5 = fig.add_subplot(gs[2, 0])
    x = np.arange(len(iterations))
    width = 0.35
    if metrics["train"]["num_runs"]:
        bars1 = ax5.bar(
            x - width / 2,
            metrics["train"]["num_runs"],
            width,
            label="Training",
            color=train_color,
            alpha=0.8,
        )
        # Add value labels on bars
        for bar in bars1:
            height = bar.get_height()
            ax5.text(
                bar.get_x() + bar.get_width() / 2.0,
                height,
                f'{int(height)}',
                ha='center',
                va='bottom',
                fontsize=10,
                fontweight='bold'
            )

    if metrics["val"]["num_runs"]:
        bars2 = ax5.bar(
            x + width / 2,
            metrics["val"]["num_runs"],
            width,
            label="Validation",
            color=val_color,
            alpha=0.8,
        )
        # Add value labels on bars
        for bar in bars2:
            height = bar.get_height()
            ax5.text(
                bar.get_x() + bar.get_width() / 2.0,
                height,
                f'{int(height)}',
                ha='center',
                va='bottom',
                fontsize=10,
                fontweight='bold'
            )

    ax5.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax5.set_ylabel("Number of Deployment Runs", fontsize=13, fontweight="bold")
    ax5.set_title("Sample Size per Iteration", fontsize=15, fontweight="bold", pad=15)
    ax5.set_xticks(x)
    ax5.set_xticklabels(iterations)
    ax5.legend(fontsize=11)
    ax5.grid(True, alpha=0.3, axis="y")

    # 6. Summary Statistics Table with Units
    ax6 = fig.add_subplot(gs[2, 1])
    ax6.axis("off")

    # Calculate improvement
    if len(metrics["train"]["success_rate"]) > 1:
        initial_success = metrics["train"]["success_rate"][0] * 100
        final_success = metrics["train"]["success_rate"][-1] * 100
        improvement = final_success - initial_success
    else:
        initial_success = final_success = improvement = 0

    if len(metrics["train"]["avg_tokens"]) > 1:
        initial_tokens = metrics["train"]["avg_tokens"][0]
        final_tokens = metrics["train"]["avg_tokens"][-1]
        token_change = ((final_tokens - initial_tokens) / initial_tokens) * 100
    else:
        initial_tokens = final_tokens = token_change = 0

    if len(metrics["train"]["avg_calls"]) > 1:
        initial_calls = metrics["train"]["avg_calls"][0]
        final_calls = metrics["train"]["avg_calls"][-1]
        calls_change = ((final_calls - initial_calls) / initial_calls) * 100
    else:
        initial_calls = final_calls = calls_change = 0

    # Summary text with units
    summary_text = f"""EXPERIMENT SUMMARY
═══════════════════════════════════════════

Iterations: {len(iterations)}
Total Runs: {sum(metrics['train']['num_runs']) + sum(metrics['val']['num_runs'])}

TRAINING PERFORMANCE:
├─ Initial Success: {initial_success:.1f}%
├─ Final Success:   {final_success:.1f}%
└─ Improvement:     {improvement:+.1f}%

TOKEN EFFICIENCY:
├─ Initial: {format_tokens(initial_tokens)} tokens/run
├─ Final:   {format_tokens(final_tokens)} tokens/run
└─ Change:  {token_change:+.1f}%

AGENT CALL EFFICIENCY:
├─ Initial: {initial_calls:.1f} calls/run
├─ Final:   {final_calls:.1f} calls/run
└─ Change:  {calls_change:+.1f}%

BEST ITERATION: #{iterations[np.argmax(metrics['train']['success_rate'])] if metrics['train']['success_rate'] else 'N/A'}
    """

    ax6.text(
        0.05,
        0.5,
        summary_text,
        fontsize=10.5,
        verticalalignment="center",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=1", facecolor="wheat", alpha=0.4, edgecolor="gray", linewidth=2),
    )

    # Overall title
    fig.suptitle(
        "E2E Prompt Optimization Results - Detailed Metrics",
        fontsize=19,
        fontweight="bold",
        y=0.985,
    )

    # Save or show
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight")
        print(f"✓ Saved visualization to: {output_path}")
    else:
        plt.tight_layout()
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Visualize e2e-optimize results with proper units"
    )
    parser.add_argument(
        "--work-dir",
        default="e2e_optimization",
        help="Working directory with experiment results",
    )
    parser.add_argument(
        "--output",
        help="Output file path (e.g., results_v2.png)",
    )

    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    if not work_dir.exists():
        print(f"Error: Work directory not found: {work_dir}")
        return 1

    print(f"Collecting metrics from: {work_dir}")
    collector = MetricsCollector(work_dir)
    metrics = collector.collect()

    if not metrics["iterations"]:
        print("No experiment data found!")
        return 1

    # Generate plot
    output_path = Path(args.output) if args.output else None
    plot_metrics(metrics, output_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
