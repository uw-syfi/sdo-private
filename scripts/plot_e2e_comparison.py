"""Compare e2e optimization experiments: rlm variants.

Reads trajectory.json files from each experiment's work directory and plots:
1. Per-iteration training success rate (best candidate per iter)
2. Validation success rate over iterations
3. Average deployment attempts per run (efficiency)
4. Average total tokens per run (cost)

Usage:
    uv run python scripts/plot_e2e_comparison.py
    uv run python scripts/plot_e2e_comparison.py --output results_comparison.png
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# --- Config -------------------------------------------------------------------

EXPERIMENTS = {
    "rlm": "e2e_optimization_rlm",
    "rlm2": "e2e_optimization_rlm2",
    "subagent": "e2e_optimization_subagent",
    "hybrid": "e2e_optimization_hybrid",
}

COLORS = {
    "rlm": "#55A868",
    "rlm2": "#4C72B0",
    "subagent": "#DD8452",
    "hybrid": "#C44E52",
}

MARKERS = {
    "rlm": "^",
    "rlm2": "o",
    "subagent": "s",
    "hybrid": "D",
}

# Regex for training run dirs: iter{N}_c{C}_{app}
TRAIN_PAT = re.compile(r"^iter(\d+)_c(\d+)_(\w+)$")
# Regex for validation run dirs: {app}_iter{N}_val
VAL_PAT = re.compile(r"^(.+)_iter(\d+)_val$")


# --- Data loading -------------------------------------------------------------


def load_trajectory(path: Path) -> dict:
    traj_file = path / ".sds" / "trajectory.json"
    if not traj_file.exists():
        return {}
    try:
        return json.loads(traj_file.read_text())
    except json.JSONDecodeError:
        return {}


def parse_experiment(work_dir: Path) -> dict:
    """Return structured data for one experiment.

    Returns:
        {
          "train": {iter: {candidate: [{"app": str, "success": bool,
                                        "attempts": int, "tokens": int}]}},
          "val":   {iter: [{"app": str, "success": bool,
                            "attempts": int, "tokens": int}]},
        }
    """
    train = defaultdict(lambda: defaultdict(list))
    val = defaultdict(list)

    if not work_dir.exists():
        return {"train": train, "val": val}

    for child in sorted(work_dir.iterdir()):
        if not child.is_dir():
            continue
        traj = load_trajectory(child)
        if not traj:
            continue

        meta = traj.get("metadata", {})
        calls = traj.get("calls", [])
        status = meta.get("status", "unknown")
        tokens = meta.get("token_usage", {}).get("total_tokens", 0)
        n_deploy = sum(1 for c in calls if c.get("phase") == "deployment")

        record = {
            "app": child.name,
            "success": status == "completed",
            "attempts": n_deploy,
            "tokens": tokens,
        }

        m = TRAIN_PAT.match(child.name)
        if m:
            iteration, candidate = int(m.group(1)), int(m.group(2))
            train[iteration][candidate].append(record)
            continue

        m = VAL_PAT.match(child.name)
        if m:
            iteration = int(m.group(2))
            val[iteration].append(record)

    return {"train": dict(train), "val": dict(val)}


def best_candidate_stats(candidates: dict) -> dict:
    """Given {candidate: [records]}, return stats for the best-scoring candidate."""
    best = None
    best_score = -1
    for _cand, records in candidates.items():
        if not records:
            continue
        score = sum(r["success"] for r in records) / len(records)
        if score > best_score:
            best_score = score
            best = records
    if best is None:
        return {"success_rate": float("nan"), "avg_attempts": float("nan"), "avg_tokens": float("nan")}
    return {
        "success_rate": sum(r["success"] for r in best) / len(best),
        "avg_attempts": sum(r["attempts"] for r in best) / len(best),
        "avg_tokens": sum(r["tokens"] for r in best) / len(best),
    }


def aggregate(data: dict) -> dict:
    """Aggregate per-iteration stats for training and validation."""
    train_stats, val_stats = {}, {}

    for it, candidates in data["train"].items():
        train_stats[it] = best_candidate_stats(candidates)

    for it, records in data["val"].items():
        if not records:
            continue
        val_stats[it] = {
            "success_rate": sum(r["success"] for r in records) / len(records),
            "avg_attempts": sum(r["attempts"] for r in records) / len(records),
            "avg_tokens": sum(r["tokens"] for r in records) / len(records),
        }

    return {"train": train_stats, "val": val_stats}


# --- Plotting -----------------------------------------------------------------


def _plot_metric(ax, all_stats: dict, phase: str, metric: str, ylabel: str, title: str, yformat=None):
    """Generic line plot for one metric across experiments."""
    has_data = False
    for exp_name, stats in all_stats.items():
        phase_stats = stats[phase]
        if not phase_stats:
            continue
        iters = sorted(phase_stats)
        vals = [phase_stats[i][metric] for i in iters]
        if all(np.isnan(v) for v in vals):
            continue
        ax.plot(
            iters,
            vals,
            marker=MARKERS[exp_name],
            color=COLORS[exp_name],
            label=exp_name,
            linewidth=2,
            markersize=7,
        )
        has_data = True

    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_xlabel("Iteration")
    ax.set_ylabel(ylabel)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    if yformat == "pct":
        ax.yaxis.set_major_formatter(ticker.PercentFormatter(xmax=1.0))
        ax.set_ylim(-0.05, 1.05)
    if has_data:
        ax.legend()
    ax.grid(True, alpha=0.3)


def plot(all_stats: dict, output: Path):
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle("E2E Optimization Experiment Comparison", fontsize=15, fontweight="bold")

    _plot_metric(
        axes[0, 0],
        all_stats,
        "train",
        "success_rate",
        "Success rate",
        "Training Success Rate (best candidate)",
        yformat="pct",
    )
    _plot_metric(
        axes[0, 1],
        all_stats,
        "val",
        "success_rate",
        "Success rate",
        "Validation Success Rate",
        yformat="pct",
    )
    _plot_metric(
        axes[1, 0],
        all_stats,
        "train",
        "avg_attempts",
        "Avg deployment attempts",
        "Avg Deployment Attempts (training, best candidate)",
    )
    _plot_metric(
        axes[1, 1],
        all_stats,
        "train",
        "avg_tokens",
        "Avg total tokens",
        "Avg Token Usage per Run (training, best candidate)",
    )

    plt.tight_layout()
    fig.savefig(output, dpi=150, bbox_inches="tight")
    print(f"Saved to {output}")


# --- Summary table ------------------------------------------------------------


def print_summary(all_stats: dict):
    print("\n=== Summary ===\n")
    for exp_name, stats in all_stats.items():
        print(f"[{exp_name}]")
        for phase in ("train", "val"):
            phase_stats = stats[phase]
            if not phase_stats:
                print(f"  {phase}: no data yet")
                continue
            for it in sorted(phase_stats):
                s = phase_stats[it]
                sr = s["success_rate"]
                at = s["avg_attempts"]
                tok = s["avg_tokens"]
                sr_str = f"{sr:.0%}" if not np.isnan(sr) else "n/a"
                at_str = f"{at:.1f}" if not np.isnan(at) else "n/a"
                tok_str = f"{tok:.0f}" if not np.isnan(tok) else "n/a"
                print(f"  {phase} iter {it}: success={sr_str}  attempts={at_str}  tokens={tok_str}")
        print()


# --- Main --------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Plot e2e experiment comparison")
    parser.add_argument(
        "--output",
        default="e2e_comparison.png",
        help="Output image file (default: e2e_comparison.png)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    all_stats = {}
    for exp_name, work_dir_name in EXPERIMENTS.items():
        work_dir = root / work_dir_name
        data = parse_experiment(work_dir)
        all_stats[exp_name] = aggregate(data)
        n_train = sum(len(recs) for candidates in data["train"].values() for recs in candidates.values())
        n_val = sum(len(recs) for recs in data["val"].values())
        print(f"{exp_name}: {n_train} training runs, {n_val} validation runs loaded from {work_dir}")

    print_summary(all_stats)
    plot(all_stats, root / args.output)


if __name__ == "__main__":
    main()
