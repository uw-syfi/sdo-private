"""DSPy configuration dataclasses.

Configuration for DSPy prompt optimization, including optimization settings,
auto-rollback parameters, and metric weights.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


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
        # Validate optimizer
        valid_optimizers = [
            "BootstrapFewShot",
            "BootstrapFewShotWithRandomSearch",
            "MIPROv2",
            "COPRO",
        ]
        if self.optimizer not in valid_optimizers:
            raise ValueError(
                f"optimizer must be one of {valid_optimizers}, got '{self.optimizer}'"
            )

        # Validate teacher_model
        if not isinstance(self.teacher_model, str) or not self.teacher_model.strip():
            raise ValueError(
                f"teacher_model must be a non-empty string, got '{self.teacher_model}'"
            )

        # Validate num_examples
        if not isinstance(self.num_examples, int):
            raise TypeError(
                f"num_examples must be int, got {type(self.num_examples).__name__}"
            )
        if self.num_examples <= 0:
            raise ValueError(f"num_examples must be positive, got {self.num_examples}")

        # Validate n_candidates
        if not isinstance(self.n_candidates, int):
            raise TypeError(
                f"n_candidates must be int, got {type(self.n_candidates).__name__}"
            )
        if self.n_candidates < 1:
            raise ValueError(f"n_candidates must be >= 1, got {self.n_candidates}")

        # Validate validation_split
        if not isinstance(self.validation_split, (int, float)):
            raise TypeError(
                f"validation_split must be numeric, got {type(self.validation_split).__name__}"
            )
        if not 0.0 <= self.validation_split < 1.0:
            raise ValueError(
                f"validation_split must be in range [0.0, 1.0), got {self.validation_split}"
            )

        # Validate metric_weights
        if not isinstance(self.metric_weights, dict):
            raise TypeError(
                f"metric_weights must be dict, got {type(self.metric_weights).__name__}"
            )

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
        if not isinstance(self.enabled, bool):
            raise TypeError(f"enabled must be bool, got {type(self.enabled).__name__}")

        if not isinstance(self.success_rate_threshold, (int, float)):
            raise TypeError(
                f"success_rate_threshold must be numeric, "
                f"got {type(self.success_rate_threshold).__name__}"
            )
        if not 0.0 <= self.success_rate_threshold <= 1.0:
            raise ValueError(
                f"success_rate_threshold must be in range [0.0, 1.0], "
                f"got {self.success_rate_threshold}"
            )

        if not isinstance(self.evaluation_window, int):
            raise TypeError(
                f"evaluation_window must be int, "
                f"got {type(self.evaluation_window).__name__}"
            )
        if self.evaluation_window <= 0:
            raise ValueError(
                f"evaluation_window must be positive, got {self.evaluation_window}"
            )


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
        if not isinstance(self.use_optimized, bool):
            raise TypeError(
                f"use_optimized must be bool, got {type(self.use_optimized).__name__}"
            )

        if not isinstance(self.use_seeds, bool):
            raise TypeError(
                f"use_seeds must be bool, got {type(self.use_seeds).__name__}"
            )

        if (
            not isinstance(self.optimized_version, str)
            or not self.optimized_version.strip()
        ):
            raise ValueError(
                f"optimized_version must be a non-empty string, "
                f"got '{self.optimized_version}'"
            )

        if not isinstance(self.fallback_to_baseline, bool):
            raise TypeError(
                f"fallback_to_baseline must be bool, "
                f"got {type(self.fallback_to_baseline).__name__}"
            )

        if not isinstance(self.enable_online_learning, bool):
            raise TypeError(
                f"enable_online_learning must be bool, "
                f"got {type(self.enable_online_learning).__name__}"
            )

        if not isinstance(self.feedback_sample_rate, (int, float)):
            raise TypeError(
                f"feedback_sample_rate must be numeric, "
                f"got {type(self.feedback_sample_rate).__name__}"
            )
        if not 0.0 <= self.feedback_sample_rate <= 1.0:
            raise ValueError(
                f"feedback_sample_rate must be in range [0.0, 1.0], "
                f"got {self.feedback_sample_rate}"
            )

        if not isinstance(self.canary_deployment, bool):
            raise TypeError(
                f"canary_deployment must be bool, "
                f"got {type(self.canary_deployment).__name__}"
            )

        if not isinstance(self.canary_percentage, (int, float)):
            raise TypeError(
                f"canary_percentage must be numeric, "
                f"got {type(self.canary_percentage).__name__}"
            )
        if not 0.0 <= self.canary_percentage <= 1.0:
            raise ValueError(
                f"canary_percentage must be in range [0.0, 1.0], "
                f"got {self.canary_percentage}"
            )

        if not isinstance(self.optimization, DSPyOptimizationConfig):
            raise TypeError(
                f"optimization must be DSPyOptimizationConfig, "
                f"got {type(self.optimization).__name__}"
            )

        if not isinstance(self.auto_rollback, DSPyAutoRollbackConfig):
            raise TypeError(
                f"auto_rollback must be DSPyAutoRollbackConfig, "
                f"got {type(self.auto_rollback).__name__}"
            )

        # Canary deployment requires use_optimized
        if self.canary_deployment and not self.use_optimized:
            raise ValueError("canary_deployment requires use_optimized=true")
