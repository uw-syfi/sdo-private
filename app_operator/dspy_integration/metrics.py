"""DSPy metrics for prompt optimization.

Metrics for evaluating prompt performance:
- DeploymentSuccessMetric: Whether deployment succeeded (historical, example-only)
- IterationEfficiencyMetric: Number of iterations/retries
- TokenEfficiencyMetric: Token usage efficiency
- PredictionQualityMetric: LLM-judge score of generated prompt quality
- HealthCheckQualityMetric: Validates health check scripts are non-trivial (prevents reward hacking)
"""

import logging
import re
from typing import Any

import dspy

logger = logging.getLogger(__name__)


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
        if abs(input_weight + output_weight - 1.0) > 1e-9:
            raise ValueError(
                f"input_weight ({input_weight}) + output_weight ({output_weight}) "
                f"must sum to 1.0, got {input_weight + output_weight}"
            )
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


class _PromptJudgeSignature(dspy.Signature):
    """You are evaluating the quality of a prompt generated for a DevOps coding agent.

    Given the deployment error context and the generated prompt, rate how well
    the prompt would guide the agent toward diagnosing and fixing the issue.

    Score guidelines:
    - 0-2: Irrelevant or missing critical information
    - 3-5: Partially addresses the error but lacks actionable specifics
    - 6-8: Clearly addresses the error with structured, actionable guidance
    - 9-10: Excellent — highly specific, well-structured, and likely to lead to a fix

    Respond with ONLY an integer from 0 to 10.
    """

    error_context: str = dspy.InputField(
        desc="The deployment error context including logs and exit codes")
    generated_prompt: str = dspy.InputField(
        desc="The generated prompt to evaluate")

    score: str = dspy.OutputField(desc="An integer score from 0 to 10")


def _parse_judge_score(raw: str) -> float:
    """Parse a 0-10 integer score from LM output into a 0.0-1.0 float.

    Extracts the first integer found in the string.  Returns 0.5 if no
    integer is present.
    """
    match = re.search(r"\d+", str(raw))
    if match:
        return min(1.0, max(0.0, int(match.group()) / 10.0))
    return 0.5


def _extract_prediction_text(prediction: Any) -> str:
    """Pull the primary text output out of a DSPy Prediction object.

    Checks common output-field names used across SDS signatures before
    falling back to str().
    """
    for field in ("rendered_prompt", "summary", "output", "system_prompt"):
        text = getattr(prediction, field, None)
        if text:
            return str(text)
    return str(prediction)


def _extract_error_context(example: Any) -> str:
    """Pull error context from a trajectory example for the judge input."""
    for field in ("error_context", "deployment_log", "health_check_output", "prompt"):
        val = getattr(example, field, None)
        if val:
            return str(val)
    return ""


class PredictionQualityMetric:
    """Scores prediction quality using an LLM judge.

    Uses a DSPy Predict module with a judging signature to evaluate how well
    the generated prompt addresses the deployment error.  The judge is lazily
    instantiated on first call so the metric can be constructed before the
    DSPy LM is configured.

    Falls back to 0.5 (neutral) on any LM or parsing error so that a single
    bad call does not crash the optimisation loop.
    """

    def __init__(self):
        self._judge = None

    @property
    def judge(self):
        if self._judge is None:
            self._judge = dspy.Predict(_PromptJudgeSignature)
        return self._judge

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate prediction quality via LLM judge.

        Args:
            example: TrajectoryExample (error_context extracted for the judge)
            prediction: Model prediction containing the generated prompt
            trace: Unused

        Returns:
            Score between 0.0 and 1.0
        """
        if prediction is None:
            return 0.0

        pred_text = _extract_prediction_text(prediction)
        if not pred_text:
            return 0.0

        error_ctx = _extract_error_context(example) or "No error context available"
        try:
            result = self.judge(
                error_context=error_ctx,
                generated_prompt=pred_text,
            )
            return _parse_judge_score(result.score)
        except Exception as e:
            logger.warning("PredictionQualityMetric judge call failed: %s", e)
            return 0.5


class GroundTruthSimilarityMetric:
    """Scores prediction quality by comparing to the ground-truth output in the example.

    Uses ``difflib.SequenceMatcher`` to compute a character-level similarity
    ratio between the predicted output and the ground-truth output field
    recorded in the trajectory.  This is the preferred prediction metric for
    COPRO / MIPROv2: it is instant (no LM call), deterministic, and immune to
    the self-evaluation bias that makes LLM judges useless when the same model
    generates both the prediction and the score.

    The ground truth lives in the dspy.Example under the output field name
    (typically ``rendered_prompt``).  If either side is missing the metric
    returns 0.0.
    """

    # Output-field names to check, in priority order.  Matches the set used by
    # ``_extract_prediction_text``.
    _OUTPUT_FIELDS = ("rendered_prompt", "summary", "output", "system_prompt")

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Compute similarity between prediction and ground truth.

        Args:
            example: dspy.Example carrying the ground-truth output field
            prediction: Model prediction (dspy.Prediction)
            trace: Unused

        Returns:
            Similarity ratio between 0.0 and 1.0
        """
        from difflib import SequenceMatcher

        if prediction is None:
            return 0.0

        pred_text = _extract_prediction_text(prediction)

        # Pull ground truth from the same field in the example
        gt_text = ""
        for field in self._OUTPUT_FIELDS:
            val = getattr(example, field, None)
            if val:
                gt_text = str(val)
                break

        if not pred_text or not gt_text:
            return 0.0

        return SequenceMatcher(None, pred_text, gt_text).ratio()


class HealthCheckQualityMetric:
    """Metric for health check script quality to prevent reward hacking.

    This metric validates that generated health_check.sh scripts are non-trivial
    and actually perform meaningful checks rather than just exiting with success.

    A trivial script that always passes would be something like:
        #!/bin/bash
        exit 0

    A legitimate health check script should:
    - Be at least 20 lines of actual code (excluding comments/blank lines)
    - Contain meaningful checks (curl, nc, docker, redis-cli, mongo, etc.)
    - Check multiple aspects (ports, endpoints, containers, databases)
    """

    # Keywords indicating real health checks
    VALID_CHECK_PATTERNS = [
        "curl",          # HTTP endpoint checks
        "nc -",          # Network/port checks
        "docker",        # Container status checks
        "redis-cli",     # Redis connectivity
        "mongo ",        # MongoDB connectivity
        "psql",          # PostgreSQL connectivity
        "mysql",         # MySQL connectivity
        "wget",          # Alternative HTTP checks
        "telnet",        # Alternative network checks
        "systemctl",     # Service status checks
        "grep -",        # Log analysis
        "ps aux",        # Process checks
    ]

    MIN_NON_TRIVIAL_LINES = 20  # Minimum lines of actual code
    MIN_CHECK_COMMANDS = 2      # Minimum number of different check types

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate health check script quality.

        Args:
            example: TrajectoryExample with health_check_script field
            prediction: Model prediction (unused)
            trace: Optional execution trace

        Returns:
            Score between 0.0 and 1.0:
            - 0.0: Trivial/always-passing script (reward hacking)
            - 1.0: Comprehensive health check script
            - 0.5: Script not available or couldn't determine quality
        """
        if not hasattr(example, "health_check_script"):
            return 0.5  # Unknown, neutral score

        script = example.health_check_script
        if script is None:
            return 0.5  # Unknown, neutral score

        if not script or len(script.strip()) < 10:
            return 0.0  # Empty or trivial

        lines = script.split("\n")

        # Count non-trivial lines (exclude comments, blank lines, shebang)
        non_trivial_lines = 0
        for line in lines:
            stripped = line.strip()
            # Skip blank lines, comments, and shebang
            if stripped and not stripped.startswith("#"):
                non_trivial_lines += 1

        # Check for trivial "exit 0" only scripts
        if non_trivial_lines <= 2:
            # Check if it's just "exit 0" or similar
            code_lines = [
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith("#")
            ]
            if any("exit 0" in line for line in code_lines) and len(code_lines) <= 2:
                return 0.0  # Reward hacking detected

        # Count number of different check command types
        check_commands_found = 0
        script_lower = script.lower()
        for pattern in self.VALID_CHECK_PATTERNS:
            if pattern.lower() in script_lower:
                check_commands_found += 1

        # Calculate score based on multiple factors
        line_score = min(1.0, non_trivial_lines / self.MIN_NON_TRIVIAL_LINES)
        check_score = min(1.0, check_commands_found / self.MIN_CHECK_COMMANDS)

        # Weighted combination: 60% lines, 40% check diversity
        quality_score = (line_score * 0.6) + (check_score * 0.4)

        return quality_score


class CompositeMetric:
    """Composite metric that combines multiple metrics with weights.

    This is the primary metric used for DSPy optimisation.  The
    prediction-quality slot (weighted by ``success_weight``) defaults to
    ``DeploymentSuccessMetric`` — which reads only historical fields from the
    example and therefore cannot differentiate between candidates.  Pass
    ``prediction_metric`` to plug in a metric that reads from the prediction:

    * ``GroundTruthSimilarityMetric()`` — compares prediction to the recorded
      ground-truth output.  Instant, deterministic, no LM call.  Preferred for
      COPRO / MIPROv2.
    * ``PredictionQualityMetric()`` — LLM judge.  Suffers from
      self-evaluation bias when the judge and the generator share the same
      model; avoid unless you can point the judge at a different provider.
    """

    def __init__(
        self,
        success_weight: float = 0.5,
        efficiency_weight: float = 0.25,
        token_weight: float = 0.15,
        health_check_weight: float = 0.1,
        max_iterations: int = 20,
        baseline_tokens: int = 10000,
        prediction_metric=None,
        include_health_check_quality: bool = True,
    ):
        """Initialize composite metric.

        Args:
            success_weight: Weight for the prediction-quality slot
            efficiency_weight: Weight for iteration efficiency
            token_weight: Weight for token efficiency
            health_check_weight: Weight for health check script quality (prevents reward hacking)
            max_iterations: Max iterations for efficiency metric
            baseline_tokens: Baseline tokens for token metric
            prediction_metric: Metric for the prediction-quality slot.
                Defaults to DeploymentSuccessMetric (backward-compatible).
                Pass PredictionQualityMetric() for LLM-judge scoring or
                GroundTruthSimilarityMetric() for deterministic comparison.
            include_health_check_quality: Whether to include health check quality in scoring.
                Recommended to prevent reward hacking (trivial health checks that always pass).
        """
        # Allow old configurations without health_check_weight
        if include_health_check_quality:
            expected_sum = success_weight + efficiency_weight + token_weight + health_check_weight
            if abs(expected_sum - 1.0) > 0.01:
                raise ValueError(
                    f"Metric weights must sum to 1.0, got {expected_sum:.3f}. "
                    f"With health_check_quality enabled, ensure all four weights sum to 1.0."
                )
        else:
            # Backward compatibility: if health check quality is disabled, only validate 3 weights
            if abs(success_weight + efficiency_weight + token_weight - 1.0) > 0.01:
                raise ValueError("Metric weights (excluding health_check) must sum to 1.0")
            health_check_weight = 0.0

        self.prediction_metric = prediction_metric or DeploymentSuccessMetric()
        self.efficiency_metric = IterationEfficiencyMetric(max_iterations)
        self.token_metric = TokenEfficiencyMetric(baseline_tokens)
        self.health_check_metric = HealthCheckQualityMetric() if include_health_check_quality else None

        self.success_weight = success_weight
        self.efficiency_weight = efficiency_weight
        self.token_weight = token_weight
        self.health_check_weight = health_check_weight
        self.include_health_check_quality = include_health_check_quality

    def __call__(self, example: Any, prediction: Any, trace: Any = None) -> float:
        """Evaluate composite metric.

        Args:
            example: TrajectoryExample
            prediction: Model prediction; returns 0.0 if None
            trace: Optional execution trace

        Returns:
            Weighted score between 0.0 and 1.0
        """
        if prediction is None:
            return 0.0

        prediction_score = self.prediction_metric(example, prediction, trace)
        efficiency_score = self.efficiency_metric(example, prediction, trace)
        token_score = self.token_metric(example, prediction, trace)

        composite_score = (
            prediction_score * self.success_weight
            + efficiency_score * self.efficiency_weight
            + token_score * self.token_weight
        )

        # Add health check quality if enabled
        if self.include_health_check_quality and self.health_check_metric:
            health_check_score = self.health_check_metric(example, prediction, trace)
            composite_score += health_check_score * self.health_check_weight

        return composite_score
