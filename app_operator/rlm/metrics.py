"""DSPy metrics for RLM optimization.

These metrics evaluate how well RLM-based prompts utilize the RLM paradigm:
- Recursion efficiency (fewer, more targeted calls)
- Context utilization (using code to filter vs sending everything)
- Token savings (comparing RLM vs direct context passing)
"""

import json
from typing import Any

from app_operator.logger import logger


class RLMEfficiencyMetric:
    """Metric for RLM recursion and call efficiency.

    Rewards prompts that:
    - Use fewer recursive calls to achieve success
    - Don't exceed reasonable recursion depth
    - Balance code execution vs recursive calls appropriately
    """

    def __init__(
        self,
        max_expected_calls: int = 10,
        max_expected_depth: int = 3,
        code_to_recursive_ratio_target: float = 2.0,
    ):
        """Initialize metric.

        Args:
            max_expected_calls: Expected max total RLM calls
            max_expected_depth: Expected max recursion depth
            code_to_recursive_ratio_target: Target ratio of code executions to recursive calls
        """
        self.max_expected_calls = max_expected_calls
        self.max_expected_depth = max_expected_depth
        self.code_to_recursive_ratio_target = code_to_recursive_ratio_target

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate RLM efficiency.

        Args:
            example: TrajectoryExample with rlm_statistics field
            prediction: Model prediction (unused)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0 (higher = more efficient)
        """
        if not hasattr(example, "rlm_statistics"):
            # Not an RLM run - return neutral score
            return 0.5

        stats = example.rlm_statistics
        if stats is None:
            return 0.5

        # Extract statistics
        total_calls = stats.get("total_calls", 0)
        code_executions = stats.get("code_executions", 0)
        recursive_calls = stats.get("recursive_calls", 0)
        max_depth = stats.get("max_depth_reached", 0)

        if total_calls == 0:
            return 0.0  # No RLM usage at all

        # Evaluate different aspects

        # 1. Total calls efficiency (fewer is better, but not zero)
        if total_calls > self.max_expected_calls:
            calls_score = max(0.0, 1.0 - ((total_calls - self.max_expected_calls) / self.max_expected_calls))
        else:
            # Optimal range: 2-max_expected_calls
            calls_score = min(1.0, total_calls / max(1, self.max_expected_calls))

        # 2. Recursion depth (not too deep)
        if max_depth > self.max_expected_depth:
            depth_score = max(0.0, 1.0 - ((max_depth - self.max_expected_depth) / self.max_expected_depth))
        else:
            depth_score = 1.0

        # 3. Balance of code vs recursive calls
        # We want more code executions (filtering) than recursive calls
        if recursive_calls > 0 and self.code_to_recursive_ratio_target > 0:
            actual_ratio = code_executions / recursive_calls
            ratio_score = min(1.0, actual_ratio / self.code_to_recursive_ratio_target)
        else:
            # All code, no recursion - that's fine too
            ratio_score = 1.0 if code_executions > 0 else 0.5

        # Weighted combination
        efficiency_score = calls_score * 0.4 + depth_score * 0.3 + ratio_score * 0.3

        # Bonus: penalize if success=False
        if hasattr(example, "success") and not example.success:
            efficiency_score *= 0.5

        return efficiency_score


class RLMContextUtilizationMetric:
    """Metric for how well RLM utilizes context filtering.

    Measures the token savings achieved by using RLM's programmatic
    context access vs feeding everything to the LLM.
    """

    def __init__(self, target_savings_ratio: float = 0.5):
        """Initialize metric.

        Args:
            target_savings_ratio: Target ratio of tokens_saved / total_context_size
                                 (0.5 means we want to save at least 50% of tokens)
        """
        self.target_savings_ratio = target_savings_ratio

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate context utilization.

        Args:
            example: TrajectoryExample with rlm_statistics field
            prediction: Model prediction (unused)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0 (higher = better context filtering).
            Returns 0.0 when RLM consumed more tokens than the single-call baseline.
        """
        if not hasattr(example, "rlm_statistics"):
            return 0.5

        stats = example.rlm_statistics
        if stats is None:
            return 0.5

        tokens_saved = stats.get("total_tokens_saved", 0)
        baseline_context_tokens = stats.get("baseline_context_tokens", 0)

        if baseline_context_tokens == 0:
            return 0.5

        savings_ratio = tokens_saved / baseline_context_tokens
        score = savings_ratio / self.target_savings_ratio
        return max(0.0, min(1.0, score))


class RLMCompositeMetric:
    """Composite metric combining RLM-specific metrics with standard metrics.

    This is the primary metric for optimizing RLM-based prompts with DSPy.
    """

    def __init__(
        self,
        # Standard weights
        success_weight: float = 0.35,
        efficiency_weight: float = 0.20,
        token_weight: float = 0.15,
        # RLM-specific weights
        rlm_efficiency_weight: float = 0.15,
        rlm_context_weight: float = 0.15,
        # Metric instances (can inject for testing)
        success_metric=None,
        efficiency_metric=None,
        token_metric=None,
        rlm_efficiency_metric=None,
        rlm_context_metric=None,
    ):
        """Initialize composite RLM metric.

        Args:
            success_weight: Weight for deployment success
            efficiency_weight: Weight for iteration efficiency
            token_weight: Weight for token efficiency
            rlm_efficiency_weight: Weight for RLM call efficiency
            rlm_context_weight: Weight for RLM context utilization
            *_metric: Optional metric instances (defaults created if None)
        """
        # Validate weights
        total_weight = success_weight + efficiency_weight + token_weight + rlm_efficiency_weight + rlm_context_weight

        if abs(total_weight - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total_weight:.3f}")

        self.success_weight = success_weight
        self.efficiency_weight = efficiency_weight
        self.token_weight = token_weight
        self.rlm_efficiency_weight = rlm_efficiency_weight
        self.rlm_context_weight = rlm_context_weight

        # Import standard metrics
        from app_operator.dspy_integration.metrics import (
            DeploymentSuccessMetric,
            IterationEfficiencyMetric,
            TokenEfficiencyMetric,
        )

        # Initialize metrics
        self.success_metric = success_metric or DeploymentSuccessMetric()
        self.efficiency_metric = efficiency_metric or IterationEfficiencyMetric()
        self.token_metric = token_metric or TokenEfficiencyMetric()
        self.rlm_efficiency_metric = rlm_efficiency_metric or RLMEfficiencyMetric()
        self.rlm_context_metric = rlm_context_metric or RLMContextUtilizationMetric()

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate composite RLM metric.

        Args:
            example: TrajectoryExample (must have rlm_statistics for RLM scoring)
            prediction: Model prediction
            trace: Optional execution trace

        Returns:
            Weighted composite score between 0.0 and 1.0
        """
        if prediction is None:
            return 0.0

        # Standard metrics
        success_score = self.success_metric(example, prediction, trace)
        efficiency_score = self.efficiency_metric(example, prediction, trace)
        token_score = self.token_metric(example, prediction, trace)

        # RLM-specific metrics
        rlm_efficiency_score = self.rlm_efficiency_metric(example, prediction, trace)
        rlm_context_score = self.rlm_context_metric(example, prediction, trace)

        # Composite score
        composite = (
            success_score * self.success_weight
            + efficiency_score * self.efficiency_weight
            + token_score * self.token_weight
            + rlm_efficiency_score * self.rlm_efficiency_weight
            + rlm_context_score * self.rlm_context_weight
        )

        # Log breakdown for debugging
        logger.debug(
            f"RLM Metric Breakdown:\n"
            f"  Success: {success_score:.3f} (weight={self.success_weight})\n"
            f"  Efficiency: {efficiency_score:.3f} (weight={self.efficiency_weight})\n"
            f"  Token: {token_score:.3f} (weight={self.token_weight})\n"
            f"  RLM Efficiency: {rlm_efficiency_score:.3f} (weight={self.rlm_efficiency_weight})\n"
            f"  RLM Context: {rlm_context_score:.3f} (weight={self.rlm_context_weight})\n"
            f"  Composite: {composite:.3f}"
        )

        return composite


def extract_rlm_statistics_from_trajectory(trajectory_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract RLM statistics from a trajectory dictionary.

    Args:
        trajectory_dict: Trajectory JSON loaded as dict

    Returns:
        Dictionary of RLM statistics
    """
    stats = {
        "total_calls": 0,
        "code_executions": 0,
        "recursive_calls": 0,
        "total_tokens_saved": 0,
        "max_depth_reached": 0,
    }

    # Look for RLM-specific messages in deployment phase
    deployment_convos = trajectory_dict.get("deployment", [])

    found_json_stats = False

    for convo in deployment_convos:
        messages = convo.get("messages", [])

        for msg in messages:
            content = msg.get("content", "")

            # Check if it's an RLM statistics message
            if "RLM Statistics:" in content:
                try:
                    # Extract JSON from message
                    json_start = content.find("{")
                    json_str = content[json_start:]
                    extracted_stats = json.loads(json_str)
                    stats.update(extracted_stats)
                    found_json_stats = True
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse RLM statistics: {e}")

            # Count RLM action messages only if no JSON stats were found
            elif not found_json_stats:
                if "[RLM execute_code" in content:
                    stats["code_executions"] += 1
                    stats["total_calls"] += 1
                elif "[RLM recursive_call" in content:
                    stats["recursive_calls"] += 1
                    stats["total_calls"] += 1

    return stats
