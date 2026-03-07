"""Metrics aggregator for trajectory analysis.

Analyzes trajectory data to compute performance metrics.
"""

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from app_operator.dspy_integration._cost import calculate_cost
from app_operator.dspy_integration._data_loader import TrajectoryDataLoader, TrajectoryExample


class MetricsAggregator:
    """Aggregate and analyze metrics from trajectories.

    Computes success rates, iteration efficiency, token costs,
    and other metrics from trajectory files.
    """

    def __init__(self, trajectories_dir: Path):
        """Initialize metrics aggregator.

        Args:
            trajectories_dir: Directory containing trajectory files
        """
        self.trajectories_dir = Path(trajectories_dir)
        self.data_loader = TrajectoryDataLoader(trajectories_dir)

    def aggregate_metrics(
        self,
        phase_filter: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate metrics from trajectories.

        Args:
            phase_filter: Optional filter for specific phase (deployment, monitoring, etc.)
            model: Model name for cost calculation (e.g., 'claude-sonnet-4-5')

        Returns:
            Dictionary of aggregated metrics
        """
        examples = self.data_loader.load_examples(phase_filter=phase_filter)

        if not examples:
            return {
                "total_examples": 0,
                "error": "No trajectory examples found",
            }

        # Aggregate by phase
        by_phase = defaultdict(list)
        for example in examples:
            by_phase[example.phase].append(example)

        metrics = {
            "total_examples": len(examples),
            "total_runs": len({ex.run_id for ex in examples}),
            "by_phase": {},
            "overall": self._compute_phase_metrics(examples, model),
        }

        # Compute metrics per phase
        for phase, phase_examples in by_phase.items():
            metrics["by_phase"][phase] = self._compute_phase_metrics(phase_examples, model)

        return metrics

    def _compute_phase_metrics(
        self,
        examples: list[TrajectoryExample],
        model: str | None = None,
    ) -> dict[str, Any]:
        """Compute metrics for a set of examples.

        Args:
            examples: List of trajectory examples
            model: Model name for cost calculation

        Returns:
            Dictionary of computed metrics
        """
        if not examples:
            return {}

        # Success metrics
        successes = [ex.success for ex in examples]
        success_rate = sum(successes) / len(successes) if successes else 0.0

        # Group by run for iteration stats — each run contributes one value
        # (all examples in the same run share the same iterations count)
        runs: dict[str, list] = defaultdict(list)
        for ex in examples:
            runs[ex.run_id].append(ex)

        iterations_list = [exs[0].iterations for exs in runs.values()]
        avg_iterations = statistics.mean(iterations_list) if iterations_list else 0.0
        median_iterations = statistics.median(iterations_list) if iterations_list else 0.0
        max_iterations = max(iterations_list) if iterations_list else 0
        min_iterations = min(iterations_list) if iterations_list else 0

        # Split runs into successful vs failed (a run succeeded if any example in it succeeded)
        successful_run_iters = [exs[0].iterations for exs in runs.values() if any(ex.success for ex in exs)]
        failed_run_iters = [exs[0].iterations for exs in runs.values() if not any(ex.success for ex in exs)]

        # Duration metrics
        durations = [ex.duration_seconds for ex in examples]
        avg_duration = statistics.mean(durations) if durations else 0.0
        total_duration = sum(durations)

        # Token metrics
        examples_with_tokens = [ex for ex in examples if ex.token_usage is not None]
        token_metrics = self._compute_token_metrics(examples_with_tokens, model)

        # Fallback metrics
        fallback_count = sum(1 for ex in examples if ex.fallback_occurred)
        fallback_rate = fallback_count / len(examples)

        # Success vs failure breakdown
        successful = [ex for ex in examples if ex.success]
        failed = [ex for ex in examples if not ex.success]

        metrics = {
            "count": len(examples),
            "success_rate": round(success_rate, 4),
            "successful_count": len(successful),
            "failed_count": len(failed),
            "iterations": {
                "avg": round(avg_iterations, 2),
                "median": median_iterations,
                "min": min_iterations,
                "max": max_iterations,
                "by_success": {
                    "successful_avg": round(statistics.mean(successful_run_iters), 2) if successful_run_iters else 0.0,
                    "failed_avg": round(statistics.mean(failed_run_iters), 2) if failed_run_iters else 0.0,
                },
            },
            "duration": {
                "avg_seconds": round(avg_duration, 2),
                "total_seconds": round(total_duration, 2),
                "total_hours": round(total_duration / 3600, 2),
            },
            "tokens": token_metrics,
            "fallback_rate": round(fallback_rate, 4),
            "fallback_count": fallback_count,
        }

        return metrics

    def _compute_token_metrics(
        self,
        examples: list[TrajectoryExample],
        model: str | None = None,
    ) -> dict[str, Any]:
        """Compute token usage and cost metrics.

        Args:
            examples: List of examples with token_usage
            model: Model name for cost calculation

        Returns:
            Token and cost metrics
        """
        if not examples:
            return {
                "available": False,
                "message": "No token usage data available",
            }

        total_input = sum((ex.token_usage or {}).get("input", 0) for ex in examples)
        total_output = sum((ex.token_usage or {}).get("output", 0) for ex in examples)
        total_tokens = total_input + total_output

        avg_input = total_input / len(examples) if examples else 0
        avg_output = total_output / len(examples) if examples else 0

        metrics = {
            "available": True,
            "total_input": total_input,
            "total_output": total_output,
            "total": total_tokens,
            "avg_input": round(avg_input, 2),
            "avg_output": round(avg_output, 2),
            "avg_total": round((total_tokens / len(examples)) if examples else 0, 2),
        }

        # Propagate estimation flag if any example used estimated counts
        if any((ex.token_usage or {}).get("estimated") for ex in examples):
            metrics["estimated"] = True

        # Calculate costs if model is provided
        if model:
            try:
                total_cost = calculate_cost(model, total_input, total_output)
                avg_cost = total_cost / len(examples) if examples else 0

                metrics["cost_usd"] = {
                    "total": round(total_cost, 4),
                    "avg_per_run": round(avg_cost, 4),
                    "model": model,
                }
            except ValueError as e:
                metrics["cost_usd"] = {
                    "error": str(e),
                }

        return metrics

    def compare_versions(
        self,
        baseline_dir: Path,
        optimized_dir: Path,
        phase_filter: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Compare metrics between baseline and optimized versions.

        Args:
            baseline_dir: Directory with baseline trajectories
            optimized_dir: Directory with optimized trajectories
            phase_filter: Optional phase filter
            model: Model name for cost calculation

        Returns:
            Comparison results with improvements/regressions
        """
        # Load metrics from both versions
        baseline_agg = MetricsAggregator(baseline_dir)
        optimized_agg = MetricsAggregator(optimized_dir)

        baseline_metrics = baseline_agg.aggregate_metrics(phase_filter, model)
        optimized_metrics = optimized_agg.aggregate_metrics(phase_filter, model)

        if "error" in baseline_metrics or "error" in optimized_metrics:
            return {
                "baseline": baseline_metrics,
                "optimized": optimized_metrics,
                "improvements": {"error": "Insufficient data for comparison"},
            }

        # Calculate improvements
        comparison = {
            "baseline": baseline_metrics,
            "optimized": optimized_metrics,
            "improvements": self._calculate_improvements(
                baseline_metrics["overall"],
                optimized_metrics["overall"],
            ),
        }

        # Per-phase comparison for phases present in both versions
        baseline_by_phase = baseline_metrics.get("by_phase", {})
        optimized_by_phase = optimized_metrics.get("by_phase", {})
        shared_phases = set(baseline_by_phase.keys()) & set(optimized_by_phase.keys())

        comparison["by_phase"] = {
            phase: {
                "baseline": baseline_by_phase[phase],
                "optimized": optimized_by_phase[phase],
                "improvements": self._calculate_improvements(
                    baseline_by_phase[phase],
                    optimized_by_phase[phase],
                ),
            }
            for phase in sorted(shared_phases)
        }

        return comparison

    def _calculate_improvements(
        self,
        baseline: dict[str, Any],
        optimized: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate percentage improvements between baseline and optimized.

        Args:
            baseline: Baseline metrics
            optimized: Optimized metrics

        Returns:
            Dictionary of improvements (positive = better, negative = worse)
        """
        improvements = {}

        # Success rate improvement
        baseline_sr = baseline.get("success_rate")
        optimized_sr = optimized.get("success_rate")
        if baseline_sr is not None and optimized_sr is not None:
            if baseline_sr > 0:
                improvements["success_rate_improvement"] = round(((optimized_sr - baseline_sr) / baseline_sr) * 100, 2)
            else:
                improvements["success_rate_improvement"] = None

        # Iteration efficiency (lower is better, so invert)
        baseline_iter = baseline.get("iterations", {}).get("avg", 0)
        optimized_iter = optimized.get("iterations", {}).get("avg", 0)
        if baseline_iter > 0:
            improvements["iteration_reduction_pct"] = round(((baseline_iter - optimized_iter) / baseline_iter) * 100, 2)

        # Token usage (lower is better)
        baseline_tokens_info = baseline.get("tokens", {})
        optimized_tokens_info = optimized.get("tokens", {})
        if baseline_tokens_info.get("available") and optimized_tokens_info.get("available"):
            baseline_tokens = baseline_tokens_info.get("total", 0)
            optimized_tokens = optimized_tokens_info.get("total", 0)
            if baseline_tokens > 0:
                improvements["token_reduction_pct"] = round(
                    ((baseline_tokens - optimized_tokens) / baseline_tokens) * 100, 2
                )

            # Cost savings (requires --model flag)
            baseline_cost = baseline_tokens_info.get("cost_usd", {})
            optimized_cost = optimized_tokens_info.get("cost_usd", {})

            if "total" in baseline_cost and "total" in optimized_cost:
                baseline_total = baseline_cost["total"]
                optimized_total = optimized_cost["total"]
                if baseline_total > 0:
                    improvements["cost_reduction_pct"] = round(
                        ((baseline_total - optimized_total) / baseline_total) * 100, 2
                    )
                    improvements["cost_savings_usd"] = round(baseline_total - optimized_total, 4)

        # Fallback rate (lower is better)
        baseline_fr = baseline.get("fallback_rate")
        optimized_fr = optimized.get("fallback_rate")
        if baseline_fr is not None and optimized_fr is not None:
            if baseline_fr > 0:
                improvements["fallback_rate_reduction_pct"] = round(
                    ((baseline_fr - optimized_fr) / baseline_fr) * 100, 2
                )
            elif optimized_fr > 0:
                # Baseline had zero fallbacks, optimized introduced some — regression
                improvements["fallback_rate_reduction_pct"] = None
            else:
                improvements["fallback_rate_reduction_pct"] = 0.0

        return improvements
