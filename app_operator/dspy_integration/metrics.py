"""DSPy metrics for prompt optimization.

Metrics for evaluating prompt performance:
- DeploymentSuccessMetric: Whether deployment succeeded
- IterationEfficiencyMetric: Number of iterations/retries
- TokenEfficiencyMetric: Token usage efficiency
"""

from typing import Any


class DeploymentSuccessMetric:
    """Metric for deployment success rate.

    Evaluates whether the deployment completed successfully based on
    exit codes and error indicators.
    """

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate deployment success.

        Args:
            example: TrajectoryExample with success field
            prediction: Model prediction (unused for now)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0 (1.0 if successful, 0.0 if failed)
        """
        if hasattr(example, "success"):
            return 1.0 if example.success else 0.0

        # Fallback: check tool calls
        if hasattr(example, "tool_calls"):
            for tool_call in reversed(example.tool_calls):
                exit_code = tool_call.get("exit_code")
                if exit_code is not None:
                    return 1.0 if exit_code == 0 else 0.0

        return 0.5  # Unknown


class IterationEfficiencyMetric:
    """Metric for iteration/retry efficiency.

    Rewards prompts that achieve success with fewer iterations.
    Uses a normalized score where fewer iterations = higher score.
    """

    def __init__(self, max_iterations: int = 20):
        """Initialize metric.

        Args:
            max_iterations: Maximum expected iterations (for normalization)
        """
        self.max_iterations = max_iterations

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate iteration efficiency.

        Args:
            example: TrajectoryExample with iterations field
            prediction: Model prediction (unused for now)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0 (higher = more efficient)
        """
        if not hasattr(example, "iterations"):
            return 0.0

        iterations = example.iterations

        # Penalize unsuccessful attempts more heavily
        if hasattr(example, "success") and not example.success:
            # Unsuccessful deployments get lower scores
            return max(0.0, 1.0 - (iterations / self.max_iterations)) * 0.5

        # For successful deployments, reward efficiency
        # 1 iteration = 1.0, max_iterations = 0.0
        if iterations <= 0:
            return 0.0

        score = 1.0 - ((iterations - 1) / max(1, self.max_iterations - 1))
        return max(0.0, min(1.0, score))


class TokenEfficiencyMetric:
    """Metric for token usage efficiency.

    Rewards prompts that use fewer tokens while still achieving success.
    Considers both input and output tokens with configurable weights.
    """

    def __init__(
        self,
        baseline_tokens: int = 10000,
        input_weight: float = 0.3,
        output_weight: float = 0.7,
    ):
        """Initialize metric.

        Args:
            baseline_tokens: Expected baseline token usage for normalization
            input_weight: Weight for input tokens (0.0-1.0)
            output_weight: Weight for output tokens (0.0-1.0)
        """
        self.baseline_tokens = baseline_tokens
        self.input_weight = input_weight
        self.output_weight = output_weight

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate token efficiency.

        Args:
            example: TrajectoryExample with token_usage field
            prediction: Model prediction (unused for now)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0 (higher = more efficient)
        """
        if not hasattr(example, "token_usage") or example.token_usage is None:
            return 0.5  # Unknown, give neutral score

        token_usage = example.token_usage
        input_tokens = token_usage.get("input", 0)
        output_tokens = token_usage.get("output", 0)

        # Calculate weighted token usage
        weighted_tokens = (
            input_tokens * self.input_weight + output_tokens * self.output_weight
        )

        # Normalize against baseline
        efficiency = max(0.0, 1.0 - (weighted_tokens / self.baseline_tokens))

        # Penalize unsuccessful attempts
        if hasattr(example, "success") and not example.success:
            efficiency *= 0.5

        return min(1.0, efficiency)


class CompositeMetric:
    """Composite metric that combines multiple metrics with weights.

    This is the primary metric used for DSPy optimization, combining
    success rate, iteration efficiency, and token efficiency.
    """

    def __init__(
        self,
        success_weight: float = 0.6,
        efficiency_weight: float = 0.25,
        token_weight: float = 0.15,
        max_iterations: int = 20,
        baseline_tokens: int = 10000,
    ):
        """Initialize composite metric.

        Args:
            success_weight: Weight for success metric
            efficiency_weight: Weight for iteration efficiency
            token_weight: Weight for token efficiency
            max_iterations: Max iterations for efficiency metric
            baseline_tokens: Baseline tokens for token metric
        """
        if abs(success_weight + efficiency_weight + token_weight - 1.0) > 0.01:
            raise ValueError("Metric weights must sum to 1.0")

        self.success_metric = DeploymentSuccessMetric()
        self.efficiency_metric = IterationEfficiencyMetric(max_iterations)
        self.token_metric = TokenEfficiencyMetric(baseline_tokens)

        self.success_weight = success_weight
        self.efficiency_weight = efficiency_weight
        self.token_weight = token_weight

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate composite metric.

        Args:
            example: TrajectoryExample
            prediction: Model prediction (unused for now)
            trace: Optional execution trace

        Returns:
            Weighted score between 0.0 and 1.0
        """
        success_score = self.success_metric(example, prediction, trace)
        efficiency_score = self.efficiency_metric(example, prediction, trace)
        token_score = self.token_metric(example, prediction, trace)

        composite_score = (
            success_score * self.success_weight
            + efficiency_score * self.efficiency_weight
            + token_score * self.token_weight
        )

        return composite_score
