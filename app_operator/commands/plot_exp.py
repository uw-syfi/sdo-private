import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from app_operator.logger import logger

console = Console()


@dataclass
class AggregatedResult:
    app: str
    success_rate: str
    success_frac: float
    deploy_iterations_mean: float | None
    deploy_iterations_min: int | None
    deploy_iterations_max: int | None
    elapsed_seconds_mean: float | None


@dataclass
class SingleResult:
    app: str
    success: bool
    status: str
    deployment_iterations: int | None
    elapsed_seconds: float | None


@dataclass
class ExperimentData:
    name: str
    is_multi_repeat: bool
    aggregated: list[AggregatedResult] = field(default_factory=list)
    single: list[SingleResult] = field(default_factory=list)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "experiments",
        nargs="+",
        help="Experiment names (e.g. with-ltm without-ltm)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Path to save chart (default: exp_config/<first-exp>/logs/plot.png). Requires matplotlib.",
    )


def _parse_success_frac(success_rate: str) -> float:
    """Parse '3/3' into 1.0, '2/3' into 0.667, etc."""
    try:
        num, denom = success_rate.split("/")
        denom_int = int(denom)
        if denom_int == 0:
            return 0.0
        return int(num) / denom_int
    except (ValueError, ZeroDivisionError):
        return 0.0


def _load_experiment(name: str) -> ExperimentData | None:
    """Load experiment results from exp_config/<name>/logs/results.json."""
    results_path = Path("exp_config") / name / "logs" / "results.json"
    if not results_path.exists():
        logger.error(f"Results file not found: {results_path}")
        return None

    try:
        with open(results_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load {results_path}: {e}")
        return None

    if "aggregated" in data:
        aggregated = []
        for entry in data["aggregated"]:
            success_rate = entry.get("success_rate", "0/0")
            aggregated.append(
                AggregatedResult(
                    app=entry["app"],
                    success_rate=success_rate,
                    success_frac=_parse_success_frac(success_rate),
                    deploy_iterations_mean=entry.get("deploy_iterations_mean"),
                    deploy_iterations_min=entry.get("deploy_iterations_min"),
                    deploy_iterations_max=entry.get("deploy_iterations_max"),
                    elapsed_seconds_mean=entry.get("elapsed_seconds_mean"),
                )
            )
        return ExperimentData(name=name, is_multi_repeat=True, aggregated=aggregated)
    else:
        single = []
        for entry in data.get("results", []):
            single.append(
                SingleResult(
                    app=entry["app"],
                    success=entry.get("success", False),
                    status=entry.get("status", "unknown"),
                    deployment_iterations=entry.get("deployment_iterations"),
                    elapsed_seconds=entry.get("elapsed_seconds"),
                )
            )
        return ExperimentData(name=name, is_multi_repeat=False, single=single)


def _normalize_to_aggregated(exp: ExperimentData) -> list[AggregatedResult]:
    """Return aggregated results, normalizing single-repeat data if needed."""
    if exp.is_multi_repeat:
        return exp.aggregated

    # Normalize single-repeat into AggregatedResult
    results = []
    for r in exp.single:
        success_rate = "1/1" if r.success else "0/1"
        iters = float(r.deployment_iterations) if r.deployment_iterations is not None else None
        results.append(
            AggregatedResult(
                app=r.app,
                success_rate=success_rate,
                success_frac=1.0 if r.success else 0.0,
                deploy_iterations_mean=iters,
                deploy_iterations_min=r.deployment_iterations,
                deploy_iterations_max=r.deployment_iterations,
                elapsed_seconds_mean=r.elapsed_seconds,
            )
        )
    return results


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def _print_single_table(exp: ExperimentData) -> None:
    """Print a flat table for a single experiment."""
    table = Table(title=f"Experiment: {exp.name}")
    table.add_column("App")
    table.add_column("Success Rate")
    table.add_column("Iter (min–max / mean)")
    table.add_column("Elapsed")

    rows = _normalize_to_aggregated(exp)
    for r in rows:
        if (
            r.deploy_iterations_min is not None
            and r.deploy_iterations_max is not None
            and r.deploy_iterations_mean is not None
        ):
            iter_str = f"{r.deploy_iterations_min}–{r.deploy_iterations_max} / {r.deploy_iterations_mean:.1f}"
        else:
            iter_str = "N/A"
        elapsed_str = _fmt_elapsed(r.elapsed_seconds_mean)
        table.add_row(r.app, r.success_rate, iter_str, elapsed_str)

    console.print(table)


def _color_best(values: list[float | None], higher_is_better: bool) -> list[str | None]:
    """Return a list of color strings (or None) for each value.

    Highlights the best value green and worst red, only when they differ.
    """
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return [None] * len(values)

    best = max(valid) if higher_is_better else min(valid)
    worst = min(valid) if higher_is_better else max(valid)

    if best == worst:
        return [None] * len(values)

    colors = []
    for v in values:
        if v is None:
            colors.append(None)
        elif v == best:
            colors.append("green")
        elif v == worst:
            colors.append("red")
        else:
            colors.append(None)
    return colors


def _print_comparison_table(experiments: list[ExperimentData]) -> None:
    """Print a flat comparison table with one group of columns per experiment."""
    table = Table(title="Experiment Comparison")
    table.add_column("App")
    for exp in experiments:
        table.add_column(f"\\[{exp.name}] Succ")
        table.add_column(f"\\[{exp.name}] Iter")

    # Gather all app names in order
    all_apps: list[str] = []
    seen: set[str] = set()
    for exp in experiments:
        for r in _normalize_to_aggregated(exp):
            if r.app not in seen:
                all_apps.append(r.app)
                seen.add(r.app)

    # Build lookup: exp_name -> app -> AggregatedResult
    lookup: dict[str, dict[str, AggregatedResult]] = {}
    for exp in experiments:
        lookup[exp.name] = {r.app: r for r in _normalize_to_aggregated(exp)}

    for app in all_apps:
        results_for_app = [lookup[exp.name].get(app) for exp in experiments]

        # Compute colors
        succ_fracs = [r.success_frac if r else None for r in results_for_app]
        iter_means = [r.deploy_iterations_mean if r else None for r in results_for_app]
        succ_colors = _color_best(succ_fracs, higher_is_better=True)
        iter_colors = _color_best(iter_means, higher_is_better=False)

        cells = [app]
        for i, r in enumerate(results_for_app):
            if r is None:
                cells.append("—")
                cells.append("—")
            else:
                succ_text = Text(r.success_rate)
                succ_color = succ_colors[i]
                if succ_color is not None:
                    succ_text.stylize(succ_color)

                if r.deploy_iterations_mean is not None:
                    iter_val = f"{r.deploy_iterations_mean:.1f}"
                else:
                    iter_val = "N/A"
                iter_text = Text(iter_val)
                iter_color = iter_colors[i]
                if iter_color is not None:
                    iter_text.stylize(iter_color)

                cells.append(succ_text)  # type: ignore[reportArgumentType]
                cells.append(iter_text)  # type: ignore[reportArgumentType]

        table.add_row(*cells)

    console.print(table)


def _save_chart(experiments: list[ExperimentData], output_path: Path) -> None:
    """Save a grouped bar chart comparing experiments to output_path."""
    try:
        import matplotlib  # type: ignore[reportMissingImports]

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[reportMissingImports]
    except ImportError:
        logger.warning("matplotlib is not installed; skipping chart generation.")
        logger.warning("Install it with: pip install matplotlib")
        return

    all_apps: list[str] = []
    seen: set[str] = set()
    for exp in experiments:
        for r in _normalize_to_aggregated(exp):
            if r.app not in seen:
                all_apps.append(r.app)
                seen.add(r.app)

    lookup: dict[str, dict[str, AggregatedResult]] = {}
    for exp in experiments:
        lookup[exp.name] = {r.app: r for r in _normalize_to_aggregated(exp)}

    n_apps = len(all_apps)
    n_exps = len(experiments)

    import numpy as np

    x = np.arange(n_apps)
    width = 0.8 / n_exps

    fig, (ax_succ, ax_iter) = plt.subplots(2, 1, figsize=(max(8, n_apps * 2), 8))
    fig.tight_layout(pad=4.0)

    for i, exp in enumerate(experiments):
        succ_vals = []
        iter_vals = []
        iter_err_low = []
        iter_err_high = []
        for app in all_apps:
            r = lookup[exp.name].get(app)
            succ_vals.append(r.success_frac * 100 if r else 0)
            mean = r.deploy_iterations_mean if r and r.deploy_iterations_mean is not None else 0
            iter_vals.append(mean)
            if r and r.deploy_iterations_min is not None and r.deploy_iterations_max is not None:
                iter_err_low.append(mean - r.deploy_iterations_min)
                iter_err_high.append(r.deploy_iterations_max - mean)
            else:
                iter_err_low.append(0)
                iter_err_high.append(0)

        offset = (i - (n_exps - 1) / 2) * width
        ax_succ.bar(x + offset, succ_vals, width, label=exp.name)
        ax_iter.bar(
            x + offset,
            iter_vals,
            width,
            label=exp.name,
            yerr=[iter_err_low, iter_err_high],
            error_kw={"capsize": 4, "elinewidth": 1.2},
        )

    ax_succ.set_title("Success Rate")
    ax_succ.set_ylabel("Success (%)")
    ax_succ.set_ylim(0, 110)
    ax_succ.set_xticks(x)
    ax_succ.set_xticklabels(all_apps)
    ax_succ.legend()
    ax_succ.axhline(100, color="gray", linestyle="--", linewidth=0.5)

    ax_iter.set_title("Mean Deployment Iterations")
    ax_iter.set_ylabel("Iterations")
    ax_iter.set_xticks(x)
    ax_iter.set_xticklabels(all_apps)
    ax_iter.legend()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"Chart saved to: {output_path}")


def run_command(args: argparse.Namespace) -> int:
    loaded = [_load_experiment(n) for n in args.experiments]
    if any(e is None for e in loaded):
        return 1

    experiments: list[ExperimentData] = [e for e in loaded if e is not None]

    if len(experiments) == 1:
        _print_single_table(experiments[0])
    else:
        _print_comparison_table(experiments)

    if args.output:
        output_path = Path(args.output)
    elif len(experiments) == 1:
        output_path = Path("exp_config") / experiments[0].name / "logs" / "plot.png"
    else:
        output_path = Path("exp_config") / experiments[0].name / "logs" / "comparison.png"

    _save_chart(experiments, output_path)
    return 0
