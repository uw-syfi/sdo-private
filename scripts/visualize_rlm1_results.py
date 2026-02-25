#!/usr/bin/env python3
"""Visualize e2e-rlm1 experiment results in the same style as visualize_e2e_results_v2.py.

Handles the e2e_optimization directory structure:
  - Training: iter{N}_c{M}_{app}   (4 candidates × 2 apps = 8 runs per iteration)
  - Validation: {app}_iter{N}_val

Token data is unavailable for RLMCodingAgent; the token panel is replaced
with average deploy attempts per run.

Usage:
    python scripts/visualize_rlm1_results.py --work-dir e2e_optimization
    python scripts/visualize_rlm1_results.py --work-dir e2e_optimization --output rlm1.png
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import matplotlib.pyplot as plt
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
    """Collect metrics from e2e_optimization trajectory files.

    Directory naming conventions handled:
      Training : iter{N}_c{M}_{app}   -> phase="train"
      Validation: {app}_iter{N}_val   -> phase="val"
    """

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.metrics: dict = defaultdict(lambda: defaultdict(list))

    def collect(self) -> Dict[str, Any]:
        exp_dirs = sorted([d for d in self.work_dir.iterdir() if d.is_dir()])

        for exp_dir in exp_dirs:
            parts = exp_dir.name.split("_")
            iter_part = [p for p in parts if p.startswith("iter")]
            if not iter_part:
                continue
            iteration = int(iter_part[0].replace("iter", ""))

            if parts[-1] == "val":
                phase = "val"
            elif parts[0].startswith("iter") and len(parts) > 1 and parts[1].startswith("c") and parts[1][1:].isdigit():
                phase = "train"
            else:
                continue

            # Read the single trajectory.json (one per experiment dir)
            traj_file = exp_dir / ".sds" / "trajectory.json"
            if not traj_file.exists():
                print(f"Warning: No trajectory.json in {exp_dir.name}")
                continue

            try:
                with open(traj_file) as f:
                    traj_data = json.load(f)
            except Exception as e:
                print(f"Warning: Failed to parse {traj_file}: {e}")
                continue

            metrics = self._extract_metrics(traj_data, exp_dir)
            metrics["phase"] = phase
            metrics["iteration"] = iteration
            metrics["exp_dir"] = exp_dir.name

            key = f"iter{iteration}_{phase}"
            self.metrics[key]["runs"].append(metrics)

        return self._aggregate_metrics()

    def _extract_metrics(self, traj_data: Dict[str, Any], exp_dir: Path) -> Dict[str, Any]:
        metadata = traj_data.get("metadata", {})
        status = metadata.get("status", "")
        success = status == "completed"

        prompt_version = "unknown"
        for call in traj_data.get("calls", []):
            if "prompt_version" in call:
                prompt_version = call["prompt_version"]
                break

        # Count deploy attempts from log files (more reliable than deployment array)
        log_dir = exp_dir / ".sds" / "logs"
        deploy_logs = len(list(log_dir.glob("deploy_attempt_*.log"))) if log_dir.exists() else 0

        # Deployment entries (LLM fix calls only)
        deployment = traj_data.get("deployment", [])
        deployment_iterations = len(deployment) if isinstance(deployment, list) else 0

        token_usage = metadata.get("token_usage", {})
        total_tokens = token_usage.get("total_tokens", 0)

        return {
            "success": success,
            "deployment_iterations": deployment_iterations,
            "deploy_attempts": deploy_logs,
            "num_agent_calls": len(traj_data.get("calls", [])),
            "prompt_version": prompt_version,
            "total_tokens": total_tokens,
        }

    def _aggregate_metrics(self) -> Dict[str, Any]:
        aggregated: Dict[str, Any] = {
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

            success_rate = sum(r["success"] for r in runs) / len(runs)
            # Avg deploy attempts for *successful* runs only (matches op1 deploy-iters metric)
            avg_iterations = float(np.mean(
                [r["deployment_iterations"] for r in runs if r["success"]] or [0]
            ))
            avg_deploy_attempts = float(np.mean([r["deploy_attempts"] for r in runs]))
            avg_calls = float(np.mean([r["num_agent_calls"] for r in runs]))
            avg_tokens = float(np.mean([r["total_tokens"] for r in runs]))

            target = aggregated[phase]
            target["success_rate"].append(success_rate)
            target["avg_iterations"].append(avg_iterations)
            target["avg_deploy_attempts"].append(avg_deploy_attempts)
            target["avg_calls"].append(avg_calls)
            target["avg_tokens"].append(avg_tokens)
            target["num_runs"].append(len(runs))

        return aggregated


def plot_metrics(metrics: Dict[str, Any], output_path: Optional[Path] = None):
    iterations: List[int] = metrics["iterations"]
    if not iterations:
        print("No data to plot!")
        return

    fig = plt.figure(figsize=(18, 14))
    gs = GridSpec(3, 2, figure=fig, hspace=0.55, wspace=0.3)

    train_color = "#2E86AB"
    val_color = "#A23B72"

    def add_value_labels(ax, xs, ys, color, fmt):
        if not ys:
            return
        y_range = max(ys) - min(ys) if len(ys) > 1 else abs(ys[0]) if ys else 1
        offset = y_range * 0.12
        for x, y in zip(xs, ys):
            ax.text(x, y + offset, fmt.format(y),
                    ha="center", va="bottom", fontsize=9, color=color, fontweight="bold")

    # ── 1. Deployment Success Rate ────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, 0])
    if metrics["train"]["success_rate"]:
        pct = [r * 100 for r in metrics["train"]["success_rate"]]
        ax1.plot(iterations, pct, marker="o", linewidth=2.5, markersize=10,
                 label="Training", color=train_color)
        add_value_labels(ax1, iterations, pct, train_color, "{:.0f}%")
    if metrics["val"]["success_rate"]:
        pct = [r * 100 for r in metrics["val"]["success_rate"]]
        ax1.plot(iterations, pct, marker="s", linewidth=2.5, markersize=10,
                 label="Validation", color=val_color)
        add_value_labels(ax1, iterations, pct, val_color, "{:.0f}%")
    ax1.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax1.set_ylabel("Success Rate (%)", fontsize=13, fontweight="bold")
    ax1.set_title("Deployment Success Rate", fontsize=15, fontweight="bold", pad=15)
    ax1.legend(fontsize=11)
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(iterations)
    ax1.set_ylim([0, 105])
    for it in iterations:
        label = "Seed Prompts" if it == 1 else f"Optimized v{it - 1}"
        ax1.text(it, 5, label, ha="center", fontsize=9, style="italic", color="gray")

    # ── 2. Token Usage (or deploy attempts if token data unavailable) ─────────
    ax2 = fig.add_subplot(gs[0, 1])
    has_tokens = any(v > 0 for v in metrics["train"].get("avg_tokens", []))
    if has_tokens:
        train_vals = [v / 1e6 for v in metrics["train"]["avg_tokens"]]
        ax2.plot(iterations, train_vals, marker="o", linewidth=2.5, markersize=10,
                 label="Training", color=train_color)
        val_vals = []
        if metrics["val"].get("avg_tokens") and any(v > 0 for v in metrics["val"]["avg_tokens"]):
            val_vals = [v / 1e6 for v in metrics["val"]["avg_tokens"]]
            ax2.plot(iterations, val_vals, marker="s", linewidth=2.5, markersize=10,
                     label="Validation", color=val_color)
        ax2.set_ylabel("Avg Tokens per Run (millions)", fontsize=13, fontweight="bold")
        ax2.set_title("Token Usage Efficiency\n(Average Tokens per Run)",
                      fontsize=13, fontweight="bold", pad=15)
        # Set ylim with enough headroom for value labels (offset = 12% of range)
        all_token_vals = train_vals + val_vals
        data_range = max(all_token_vals) - min(all_token_vals) if len(all_token_vals) > 1 else abs(all_token_vals[0]) if all_token_vals else 1
        ax2.set_ylim(min(0, min(all_token_vals)), max(all_token_vals) + data_range * 0.30)
        add_value_labels(ax2, iterations, train_vals, train_color, "{:.2f}M")
        if val_vals:
            add_value_labels(ax2, iterations, val_vals, val_color, "{:.2f}M")
    else:
        all_attempt_vals = []
        if metrics["train"]["avg_deploy_attempts"]:
            vals = metrics["train"]["avg_deploy_attempts"]
            all_attempt_vals += vals
            ax2.plot(iterations, vals, marker="o", linewidth=2.5, markersize=10,
                     label="Training", color=train_color)
        if metrics["val"]["avg_deploy_attempts"]:
            vals = metrics["val"]["avg_deploy_attempts"]
            all_attempt_vals += vals
            ax2.plot(iterations, vals, marker="s", linewidth=2.5, markersize=10,
                     label="Validation", color=val_color)
        ax2.set_ylabel("Avg Deploy Attempts per Run", fontsize=13, fontweight="bold")
        ax2.set_title("Deployment Attempt Volume\n(token data unavailable for RLMCodingAgent)",
                      fontsize=13, fontweight="bold", pad=15)
        if all_attempt_vals:
            data_range = max(all_attempt_vals) - min(all_attempt_vals) if len(all_attempt_vals) > 1 else abs(all_attempt_vals[0])
            ax2.set_ylim(min(0, min(all_attempt_vals)), max(all_attempt_vals) + data_range * 0.30)
        if metrics["train"]["avg_deploy_attempts"]:
            add_value_labels(ax2, iterations, metrics["train"]["avg_deploy_attempts"], train_color, "{:.1f}")
        if metrics["val"]["avg_deploy_attempts"]:
            add_value_labels(ax2, iterations, metrics["val"]["avg_deploy_attempts"], val_color, "{:.1f}")
    ax2.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax2.legend(fontsize=11)
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(iterations)

    # ── 3. Deployment Efficiency ──────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    if metrics["train"]["avg_iterations"]:
        vals = metrics["train"]["avg_iterations"]
        ax3.plot(iterations, vals, marker="o", linewidth=2.5, markersize=10,
                 label="Training", color=train_color)
        y_range = max(vals) - min(vals) if len(vals) > 1 else max(vals)
        off = max(y_range * 0.1, 0.2)
        for x, y in zip(iterations, vals):
            ax3.text(x, y + off, f"{y:.1f}", ha="center", va="bottom",
                     fontsize=9, color=train_color, fontweight="bold")
    if metrics["val"]["avg_iterations"]:
        vals = metrics["val"]["avg_iterations"]
        ax3.plot(iterations, vals, marker="s", linewidth=2.5, markersize=10,
                 label="Validation", color=val_color)
        y_range = max(vals) - min(vals) if len(vals) > 1 else max(vals)
        off = max(y_range * 0.1, 0.2)
        for x, y in zip(iterations, vals):
            ax3.text(x, y + off, f"{y:.1f}", ha="center", va="bottom",
                     fontsize=9, color=val_color, fontweight="bold")
    ax3.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax3.set_ylabel("Avg Fix-Deploy Iterations", fontsize=13, fontweight="bold")
    ax3.set_title("Deployment Efficiency (Lower = Better)", fontsize=15, fontweight="bold", pad=15)
    ax3.legend(fontsize=11)
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(iterations)

    # ── 4. LLM Invocations per Run ────────────────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 1])
    if metrics["train"]["avg_calls"]:
        vals = metrics["train"]["avg_calls"]
        ax4.plot(iterations, vals, marker="o", linewidth=2.5, markersize=10,
                 label="Training", color=train_color)
        y_range = max(vals) - min(vals) if len(vals) > 1 else max(vals)
        off = max(y_range * 0.1, 0.3)
        for x, y in zip(iterations, vals):
            ax4.text(x, y + off, f"{y:.1f}", ha="center", va="bottom",
                     fontsize=9, color=train_color, fontweight="bold")
    if metrics["val"]["avg_calls"]:
        vals = metrics["val"]["avg_calls"]
        ax4.plot(iterations, vals, marker="s", linewidth=2.5, markersize=10,
                 label="Validation", color=val_color)
        y_range = max(vals) - min(vals) if len(vals) > 1 else max(vals)
        off = max(y_range * 0.1, 0.3)
        for x, y in zip(iterations, vals):
            ax4.text(x, y + off, f"{y:.1f}", ha="center", va="bottom",
                     fontsize=9, color=val_color, fontweight="bold")
    ax4.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax4.set_ylabel("LLM Invocations per Run", fontsize=13, fontweight="bold")
    ax4.set_title("Agent Call Frequency (Lower = Faster Resolution)",
                  fontsize=15, fontweight="bold", pad=15)
    ax4.legend(fontsize=11)
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(iterations)
    ax4.text(0.5, 0.02,
             "Note: Each call = 1 LLM invocation (code analysis, script gen, error fix, or monitoring)",
             transform=ax4.transAxes, ha="center", fontsize=9, style="italic", color="gray")

    # ── 5. Sample Size per Iteration ─────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 0])
    x = np.arange(len(iterations))
    width = 0.35
    if metrics["train"]["num_runs"]:
        bars1 = ax5.bar(x - width / 2, metrics["train"]["num_runs"], width,
                        label="Training", color=train_color, alpha=0.8)
        for bar in bars1:
            h = bar.get_height()
            ax5.text(bar.get_x() + bar.get_width() / 2, h, f"{int(h)}",
                     ha="center", va="bottom", fontsize=10, fontweight="bold")
    if metrics["val"]["num_runs"]:
        bars2 = ax5.bar(x + width / 2, metrics["val"]["num_runs"], width,
                        label="Validation", color=val_color, alpha=0.8)
        for bar in bars2:
            h = bar.get_height()
            ax5.text(bar.get_x() + bar.get_width() / 2, h, f"{int(h)}",
                     ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax5.set_xlabel("Optimization Iteration", fontsize=13, fontweight="bold")
    ax5.set_ylabel("Number of Deployment Runs", fontsize=13, fontweight="bold")
    ax5.set_title("Sample Size per Iteration", fontsize=15, fontweight="bold", pad=15)
    ax5.set_xticks(x)
    ax5.set_xticklabels(iterations)
    ax5.legend(fontsize=11)
    ax5.grid(True, alpha=0.3, axis="y")

    # ── 6. Summary Statistics ─────────────────────────────────────────────────
    ax6 = fig.add_subplot(gs[2, 1])
    ax6.axis("off")

    train_sr = metrics["train"]["success_rate"]
    train_calls = metrics["train"]["avg_calls"]
    train_tokens = metrics["train"].get("avg_tokens", [])

    if len(train_sr) > 1:
        initial_success = train_sr[0] * 100
        final_success = train_sr[-1] * 100
        improvement = final_success - initial_success
    else:
        initial_success = final_success = improvement = 0

    if len(train_calls) > 1:
        initial_calls = train_calls[0]
        final_calls = train_calls[-1]
        calls_change = ((final_calls - initial_calls) / initial_calls * 100) if initial_calls else 0
    else:
        initial_calls = final_calls = calls_change = 0

    has_tokens = any(v > 0 for v in train_tokens)
    if has_tokens and len(train_tokens) > 1:
        initial_tok = train_tokens[0] / 1e6
        final_tok = train_tokens[-1] / 1e6
        tok_change = ((final_tok - initial_tok) / initial_tok * 100) if initial_tok else 0
        token_section = (
            f"TOKEN EFFICIENCY:\n"
            f"├─ Initial: {initial_tok:.2f}M tokens/run\n"
            f"├─ Final:   {final_tok:.2f}M tokens/run\n"
            f"└─ Change:  {tok_change:+.1f}%"
        )
    else:
        token_section = "TOKEN EFFICIENCY:\n└─ Data unavailable (upgrade required)"

    best_iter = iterations[int(np.argmax(train_sr))] if train_sr else "N/A"
    total_runs = sum(metrics["train"]["num_runs"]) + sum(metrics["val"]["num_runs"])

    summary_text = f"""EXPERIMENT SUMMARY
═══════════════════════════════════════════

Agent:      RLMCodingAgent
Iterations: {len(iterations)}
Total Runs: {total_runs}

TRAINING PERFORMANCE:
├─ Initial Success: {initial_success:.1f}%
├─ Final Success:   {final_success:.1f}%
└─ Improvement:     {improvement:+.1f}%

{token_section}

AGENT CALL EFFICIENCY:
├─ Initial: {initial_calls:.1f} calls/run
├─ Final:   {final_calls:.1f} calls/run
└─ Change:  {calls_change:+.1f}%

BEST ITERATION: #{best_iter}
    """

    ax6.text(
        0.05, 0.97, summary_text,
        fontsize=10, verticalalignment="top", fontfamily="monospace",
        transform=ax6.transAxes,
        bbox=dict(boxstyle="round,pad=0.8", facecolor="wheat", alpha=0.4,
                  edgecolor="gray", linewidth=2),
    )

    fig.suptitle(
        "E2E Prompt Optimization Results - Detailed Metrics (RLM1)",
        fontsize=19, fontweight="bold", y=0.985,
    )

    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches="tight")
        print(f"Saved visualization to: {output_path}")
    else:
        plt.tight_layout()
        plt.show()


def main():
    parser = argparse.ArgumentParser(description="Visualize e2e-rlm1 results")
    parser.add_argument("--work-dir", default="e2e_optimization",
                        help="Working directory with experiment results")
    parser.add_argument("--output", help="Output file path (e.g., rlm1.png)")
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

    output_path = Path(args.output) if args.output else None
    plot_metrics(metrics, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
