#!/usr/bin/env python3
"""Visualize metrics from e2e-optimize experiment results.

Usage:
    python scripts/visualize_e2e_results.py --work-dir opt1
    python scripts/visualize_e2e_results.py --work-dir opt1 --output results.png
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
            # Handle dict case (keyed by iteration number)
            metrics["deployment_iterations"] = len(deployment)

        # Count agent calls (all phases)
        metrics["num_agent_calls"] = len(traj_data.get("calls", []))

        # Load token usage from gemini_sessions files
        gemini_sessions = traj_data.get("gemini_sessions", [])
        if isinstance(gemini_sessions, list):
            for session_path in gemini_sessions:
                # Remove "trajectories/" prefix if present since traj_dir already points there
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
                        # Silently skip session files that can't be loaded
                        pass

        # Check health check quality (simple heuristic)
        if isinstance(deployment, list):
            for deploy_attempt in deployment:
                for msg in deploy_attempt.get("messages", []):
                    content = ""
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                    elif isinstance(msg, str):
                        content = msg
                    if "health_check.sh" in content and len(content) > 500:
                        metrics["health_check_quality"] = 1
        elif isinstance(deployment, dict):
            for deploy_attempt in deployment.values():
                for msg in deploy_attempt.get("messages", []):
                    content = ""
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                    elif isinstance(msg, str):
                        content = msg
                    if "health_check.sh" in content and len(content) > 500:
                        metrics["health_check_quality"] = 1

        return metrics

    def _aggregate_metrics(self) -> Dict[str, Any]:
        """Aggregate metrics by iteration and phase."""
        aggregated = {
            "iterations": [],
            "train": defaultdict(list),
            "val": defaultdict(list),
            "raw": self.metrics,
        }

        # Group by iteration
        for key, data in sorted(self.metrics.items()):
            iter_num = int(key.split("_")[0].replace("iter", ""))
            phase = key.split("_")[1]

            if iter_num not in aggregated["iterations"]:
                aggregated["iterations"].append(iter_num)

            runs = data.get("runs", [])
            if not runs:
                continue

            # Aggregate metrics for this iteration/phase
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


def plot_metrics(metrics: Dict[str, Any], output_path: Optional[Path] = None):
    """Create comprehensive visualization of metrics."""
    iterations = metrics["iterations"]
    if not iterations:
        print("No data to plot!")
        return

    # Create figure with subplots
    fig = plt.figure(figsize=(16, 12))
    gs = GridSpec(3, 2, figure=fig, hspace=0.3, wspace=0.3)

    # Color scheme
    train_color = "#2E86AB"  # Blue
    val_color = "#A23B72"  # Purple
    seed_color = "#F18F01"  # Orange

    # 1. Success Rate Over Iterations
    ax1 = fig.add_subplot(gs[0, 0])
    if metrics["train"]["success_rate"]:
        ax1.plot(
            iterations,
            [r * 100 for r in metrics["train"]["success_rate"]],
            marker="o",
            linewidth=2,
            markersize=8,
            label="Training",
            color=train_color,
        )
    if metrics["val"]["success_rate"]:
        ax1.plot(
            iterations,
            [r * 100 for r in metrics["val"]["success_rate"]],
            marker="s",
            linewidth=2,
            markersize=8,
            label="Validation",
            color=val_color,
        )
    ax1.set_xlabel("Iteration", fontsize=12)
    ax1.set_ylabel("Success Rate (%)", fontsize=12)
    ax1.set_title("Deployment Success Rate", fontsize=14, fontweight="bold")
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(iterations)
    ax1.set_ylim([0, 105])

    # Add version labels
    for i, iter_num in enumerate(iterations):
        version = "Seeds" if iter_num == 1 else f"v{iter_num-1}"
        ax1.text(
            iter_num,
            5,
            version,
            ha="center",
            fontsize=9,
            style="italic",
            color="gray",
        )

    # 2. Token Usage Over Iterations
    ax2 = fig.add_subplot(gs[0, 1])
    if metrics["train"]["avg_tokens"]:
        ax2.plot(
            iterations,
            metrics["train"]["avg_tokens"],
            marker="o",
            linewidth=2,
            markersize=8,
            label="Training",
            color=train_color,
        )
    if metrics["val"]["avg_tokens"]:
        ax2.plot(
            iterations,
            metrics["val"]["avg_tokens"],
            marker="s",
            linewidth=2,
            markersize=8,
            label="Validation",
            color=val_color,
        )
    ax2.set_xlabel("Iteration", fontsize=12)
    ax2.set_ylabel("Average Tokens per Run", fontsize=12)
    ax2.set_title("Token Usage Efficiency", fontsize=14, fontweight="bold")
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(iterations)

    # 3. Deployment Iterations to Success
    ax3 = fig.add_subplot(gs[1, 0])
    if metrics["train"]["avg_iterations"]:
        ax3.plot(
            iterations,
            metrics["train"]["avg_iterations"],
            marker="o",
            linewidth=2,
            markersize=8,
            label="Training",
            color=train_color,
        )
    if metrics["val"]["avg_iterations"]:
        ax3.plot(
            iterations,
            metrics["val"]["avg_iterations"],
            marker="s",
            linewidth=2,
            markersize=8,
            label="Validation",
            color=val_color,
        )
    ax3.set_xlabel("Iteration", fontsize=12)
    ax3.set_ylabel("Avg Deployment Iterations", fontsize=12)
    ax3.set_title(
        "Deployment Efficiency (Fewer is Better)", fontsize=14, fontweight="bold"
    )
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(iterations)

    # 4. Agent Calls per Run
    ax4 = fig.add_subplot(gs[1, 1])
    if metrics["train"]["avg_calls"]:
        ax4.plot(
            iterations,
            metrics["train"]["avg_calls"],
            marker="o",
            linewidth=2,
            markersize=8,
            label="Training",
            color=train_color,
        )
    if metrics["val"]["avg_calls"]:
        ax4.plot(
            iterations,
            metrics["val"]["avg_calls"],
            marker="s",
            linewidth=2,
            markersize=8,
            label="Validation",
            color=val_color,
        )
    ax4.set_xlabel("Iteration", fontsize=12)
    ax4.set_ylabel("Average Agent Calls", fontsize=12)
    ax4.set_title("Agent Call Frequency", fontsize=14, fontweight="bold")
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(iterations)

    # 5. Run Count Distribution
    ax5 = fig.add_subplot(gs[2, 0])
    x = np.arange(len(iterations))
    width = 0.35
    if metrics["train"]["num_runs"]:
        ax5.bar(
            x - width / 2,
            metrics["train"]["num_runs"],
            width,
            label="Training",
            color=train_color,
            alpha=0.8,
        )
    if metrics["val"]["num_runs"]:
        ax5.bar(
            x + width / 2,
            metrics["val"]["num_runs"],
            width,
            label="Validation",
            color=val_color,
            alpha=0.8,
        )
    ax5.set_xlabel("Iteration", fontsize=12)
    ax5.set_ylabel("Number of Runs", fontsize=12)
    ax5.set_title("Runs per Iteration", fontsize=14, fontweight="bold")
    ax5.set_xticks(x)
    ax5.set_xticklabels(iterations)
    ax5.legend()
    ax5.grid(True, alpha=0.3, axis="y")

    # 6. Summary Statistics Table
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

    # Summary text
    summary_text = f"""
    EXPERIMENT SUMMARY
    ═══════════════════════════════════

    Iterations Completed: {len(iterations)}
    Total Runs: {sum(metrics['train']['num_runs']) + sum(metrics['val']['num_runs'])}

    TRAINING PERFORMANCE:
    • Initial Success Rate: {initial_success:.1f}%
    • Final Success Rate: {final_success:.1f}%
    • Improvement: {improvement:+.1f}%

    EFFICIENCY:
    • Initial Avg Tokens: {initial_tokens:.0f}
    • Final Avg Tokens: {final_tokens:.0f}
    • Change: {token_change:+.1f}%

    BEST ITERATION: {iterations[np.argmax(metrics['train']['success_rate'])] if metrics['train']['success_rate'] else 'N/A'}
    """

    ax6.text(
        0.1,
        0.5,
        summary_text,
        fontsize=11,
        verticalalignment="center",
        fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.3),
    )

    # Overall title
    fig.suptitle(
        "E2E Prompt Optimization Results",
        fontsize=18,
        fontweight="bold",
        y=0.98,
    )

    # Save or show
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight")
        print(f"✓ Saved visualization to: {output_path}")
    else:
        plt.tight_layout()
        plt.show()


def print_summary(metrics: Dict[str, Any]):
    """Print text summary of results."""
    print("\n" + "=" * 60)
    print("E2E OPTIMIZATION SUMMARY")
    print("=" * 60)

    for phase in ["train", "val"]:
        if not metrics[phase]["success_rate"]:
            continue

        print(f"\n{phase.upper()}:")
        for i, iter_num in enumerate(metrics["iterations"]):
            version = "Seeds" if iter_num == 1 else f"v{iter_num-1}"
            success = metrics[phase]["success_rate"][i] * 100
            tokens = metrics[phase]["avg_tokens"][i]
            iters = metrics[phase]["avg_iterations"][i]

            print(
                f"  Iteration {iter_num} ({version:6s}): "
                f"Success={success:5.1f}%  "
                f"Tokens={tokens:7.0f}  "
                f"DeployIters={iters:4.1f}"
            )

    print("\n" + "=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Visualize e2e-optimize experiment results"
    )
    parser.add_argument(
        "--work-dir",
        default="e2e_optimization",
        help="Working directory with experiment results",
    )
    parser.add_argument(
        "--output",
        help="Output file path (e.g., results.png). If not specified, shows interactive plot.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Print summary only, don't generate plot",
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

    # Print summary
    print_summary(metrics)

    # Generate plot
    if not args.no_plot:
        output_path = Path(args.output) if args.output else None
        plot_metrics(metrics, output_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
