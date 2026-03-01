"""DSPy configuration dataclasses.

Configuration for DSPy prompt optimization, including optimization settings,
auto-rollback parameters, and metric weights.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from app_operator.validation import (
    validate_field,
    validate_type,
    validate_range,
)

# Label used for (int, float) type checks to match the existing "numeric" wording.
_NUMERIC_LABEL = "numeric"


@dataclass
class DSPyOptimizationConfig:
    """Configuration for DSPy optimization process.

    Attributes:
        optimizer: DSPy optimizer to use (e.g., 'BootstrapFewShot', 'MIPRO')
        teacher_model: Model to use for generating training examples
        num_examples: Number of examples for few-shot optimization
        validation_split: Fraction of data reserved for validation (0.0-1.0)
        metric_weights: Weights for different metrics (must sum to 1.0)
    """

    VALID_OPTIMIZERS = [
        "BootstrapFewShot",
        "BootstrapFewShotWithRandomSearch",
        "MIPROv2",
        "COPRO",
    ]

    optimizer: str = "BootstrapFewShot"
    teacher_model: str = "claude-sonnet-4-5"
    num_examples: int = 30
    validation_split: float = 0.2
    n_candidates: int = 4
    metric_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "success": 0.5,
            "efficiency": 0.25,
            "tokens": 0.15,
            "health_check": 0.1,
        }
    )

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.optimizer not in self.VALID_OPTIMIZERS:
            raise ValueError(
                f"optimizer must be one of {self.VALID_OPTIMIZERS}, got '{self.optimizer}'"
            )

        # teacher_model: non-empty string
        if not isinstance(self.teacher_model, str) or not self.teacher_model.strip():
            raise ValueError(
                f"teacher_model must be a non-empty string, got '{self.teacher_model}'"
            )

        validate_field(self.num_examples, "num_examples", int, positive=True)
        validate_field(self.n_candidates, "n_candidates", int, min_val=1)

        # validation_split: numeric in [0.0, 1.0)
        validate_type(
            self.validation_split, "validation_split", (int, float),
            type_label=_NUMERIC_LABEL,
        )
        validate_range(
            self.validation_split,
            "validation_split",
            min_val=0.0,
            max_val=1.0,
            max_exclusive=True,
        )

        # Validate metric_weights
        validate_type(self.metric_weights, "metric_weights", dict)

        # Support both old format (3 weights) and new format (4 weights with health_check)
        required_metrics_new = {"success", "efficiency", "tokens", "health_check"}
        required_metrics_old = {"success", "efficiency", "tokens"}

        provided_metrics = set(self.metric_weights.keys())
        if provided_metrics not in (required_metrics_old, required_metrics_new):
            raise ValueError(
                f"metric_weights must contain either {required_metrics_old} (legacy) "
                f"or {required_metrics_new} (with health check quality), "
                f"got {provided_metrics}"
            )

        for metric, weight in self.metric_weights.items():
            if not isinstance(weight, (int, float)):
                raise TypeError(
                    f"metric_weights['{metric}'] must be numeric, "
                    f"got {type(weight).__name__}"
                )
            if not 0.0 <= weight <= 1.0:
                raise ValueError(
                    f"metric_weights['{metric}'] must be in range [0.0, 1.0], "
                    f"got {weight}"
                )

        total_weight = sum(self.metric_weights.values())
        if not (0.99 <= total_weight <= 1.01):  # Allow small floating point error
            raise ValueError(f"metric_weights must sum to 1.0, got {total_weight:.3f}")


@dataclass
class DSPyAutoRollbackConfig:
    """Configuration for automatic rollback on performance degradation.

    Attributes:
        enabled: Whether to enable automatic rollback
        success_rate_threshold: Rollback if success rate drops by this fraction
        evaluation_window: Number of recent runs to evaluate
    """

    enabled: bool = True
    success_rate_threshold: float = 0.05
    evaluation_window: int = 100

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_field(self.enabled, "enabled", bool)

        validate_type(
            self.success_rate_threshold, "success_rate_threshold", (int, float),
            type_label=_NUMERIC_LABEL,
        )
        validate_range(
            self.success_rate_threshold,
            "success_rate_threshold",
            min_val=0.0,
            max_val=1.0,
        )

        validate_field(self.evaluation_window, "evaluation_window", int, positive=True)


@dataclass
class DSPyConfig:
    """Configuration for DSPy prompt optimization.

    Attributes:
        use_optimized: Whether to use optimized prompts (default: False)
        optimized_version: Version of optimized prompts to use (e.g., 'v1', 'latest')
        runtime_model: Model to use for runtime DSPy invocation (auto-populated from agent.model)
        fallback_to_baseline: Fall back to Jinja2 if DSPy fails (default: True)
        enable_online_learning: Enable feedback collection during runs
        feedback_sample_rate: Fraction of runs to collect feedback from (0.0-1.0)
        canary_deployment: Enable canary deployment (gradual rollout)
        canary_percentage: Percentage of runs to use optimized prompts (0.0-1.0)
        optimization: Optimization process configuration
        auto_rollback: Automatic rollback configuration
    """

    use_optimized: bool = False
    use_seeds: bool = False
    optimized_version: str = "latest"
    runtime_model: Optional[str] = None
    vertex_location: Optional[str] = None
    fallback_to_baseline: bool = True
    enable_online_learning: bool = False
    feedback_sample_rate: float = 0.1
    canary_deployment: bool = False
    canary_percentage: float = 0.0
    optimization: DSPyOptimizationConfig = field(default_factory=DSPyOptimizationConfig)
    auto_rollback: DSPyAutoRollbackConfig = field(
        default_factory=DSPyAutoRollbackConfig
    )

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_field(self.use_optimized, "use_optimized", bool)
        validate_field(self.use_seeds, "use_seeds", bool)

        if (
            not isinstance(self.optimized_version, str)
            or not self.optimized_version.strip()
        ):
            raise ValueError(
                f"optimized_version must be a non-empty string, "
                f"got '{self.optimized_version}'"
            )

        validate_field(self.fallback_to_baseline, "fallback_to_baseline", bool)
        validate_field(self.enable_online_learning, "enable_online_learning", bool)

        validate_type(
            self.feedback_sample_rate, "feedback_sample_rate", (int, float),
            type_label=_NUMERIC_LABEL,
        )
        validate_range(
            self.feedback_sample_rate,
            "feedback_sample_rate",
            min_val=0.0,
            max_val=1.0,
        )

        validate_field(self.canary_deployment, "canary_deployment", bool)

        validate_type(
            self.canary_percentage, "canary_percentage", (int, float),
            type_label=_NUMERIC_LABEL,
        )
        validate_range(
            self.canary_percentage,
            "canary_percentage",
            min_val=0.0,
            max_val=1.0,
        )

        validate_field(self.optimization, "optimization", DSPyOptimizationConfig)
        validate_field(self.auto_rollback, "auto_rollback", DSPyAutoRollbackConfig)

        # Canary deployment requires use_optimized
        if self.canary_deployment and not self.use_optimized:
            raise ValueError("canary_deployment requires use_optimized=true")
