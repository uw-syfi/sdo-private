try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[reportMissingImports]
from dataclasses import dataclass, field, fields
from pathlib import Path

from app_operator.exceptions import ConfigurationError
from app_operator.logger import logger
from app_operator.validation import (
    validate_dataclass_fields,
    validate_field,
    validate_range,
    validate_type,
)


class UnrecognizedSectionError(ConfigurationError):
    """Raised when an unrecognized section is found in the config file."""


class UnrecognizedFieldError(ConfigurationError):
    """Raised when an unrecognized field is found in a recognized section."""


# ---------------------------------------------------------------------------
# DSPy configuration dataclasses
# Defined here (in the foundational config module) so that app_operator.config
# does not need to import from app_operator.dspy_integration.
# app_operator/dspy_integration/config.py re-exports these for backward compat.
# ---------------------------------------------------------------------------

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
    VALID_SELECTION_MODES = [
        "score",
        "hybrid",
        "llm",
    ]

    optimizer: str = "BootstrapFewShot"
    teacher_model: str = "claude-sonnet-4-5"
    num_examples: int = 30
    validation_split: float = 0.2
    n_candidates: int = 4
    selection_mode: str = "hybrid"
    selection_top_k: int = 3
    metric_weights: dict[str, float] = field(
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
            raise ValueError(f"optimizer must be one of {self.VALID_OPTIMIZERS}, got '{self.optimizer}'")

        # teacher_model: non-empty string
        if not isinstance(self.teacher_model, str) or not self.teacher_model.strip():
            raise ValueError(f"teacher_model must be a non-empty string, got '{self.teacher_model}'")

        validate_field(self.num_examples, "num_examples", int, positive=True)
        validate_field(self.n_candidates, "n_candidates", int, min_val=1)
        validate_field(self.selection_mode, "selection_mode", str)
        if self.selection_mode not in self.VALID_SELECTION_MODES:
            raise ValueError(f"selection_mode must be one of {self.VALID_SELECTION_MODES}, got '{self.selection_mode}'")
        validate_field(self.selection_top_k, "selection_top_k", int, min_val=1)

        # validation_split: numeric in [0.0, 1.0)
        validate_type(
            self.validation_split,
            "validation_split",
            (int, float),
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
                raise TypeError(f"metric_weights['{metric}'] must be numeric, got {type(weight).__name__}")
            if not 0.0 <= weight <= 1.0:
                raise ValueError(f"metric_weights['{metric}'] must be in range [0.0, 1.0], got {weight}")

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
            self.success_rate_threshold,
            "success_rate_threshold",
            (int, float),
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
    runtime_model: str | None = None
    vertex_location: str | None = None
    fallback_to_baseline: bool = True
    enable_online_learning: bool = False
    feedback_sample_rate: float = 0.1
    canary_deployment: bool = False
    canary_percentage: float = 0.0
    optimization: DSPyOptimizationConfig = field(default_factory=DSPyOptimizationConfig)
    auto_rollback: DSPyAutoRollbackConfig = field(default_factory=DSPyAutoRollbackConfig)

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_field(self.use_optimized, "use_optimized", bool)
        validate_field(self.use_seeds, "use_seeds", bool)

        if not isinstance(self.optimized_version, str) or not self.optimized_version.strip():
            raise ValueError(f"optimized_version must be a non-empty string, got '{self.optimized_version}'")

        validate_field(self.fallback_to_baseline, "fallback_to_baseline", bool)
        validate_field(self.enable_online_learning, "enable_online_learning", bool)

        validate_type(
            self.feedback_sample_rate,
            "feedback_sample_rate",
            (int, float),
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
            self.canary_percentage,
            "canary_percentage",
            (int, float),
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


# ---------------------------------------------------------------------------
# FaultInjection configuration dataclass
# Defined here (in the foundational config module) so that app_operator.config
# does not need to import from app_operator.fault_injection.
# app_operator/fault_injection/config.py re-exports this for backward compat.
#
# Note: valid category/severity values are hardcoded here as plain strings to
# avoid importing FaultCategory/FaultSeverity from app_operator.fault_injection.
# The enums in fault_injection.models must stay consistent with these sets.
# ---------------------------------------------------------------------------

_FAULT_VALID_CATEGORIES = {
    "misconfiguration",
    "security",
    "metastable",
    "correlated",
    "infrastructure",
}

_FAULT_VALID_SEVERITIES = {
    "low",
    "medium",
    "high",
    "critical",
}


@dataclass
class FaultInjectionConfig:
    """Configuration for fault injection in training data collection.

    Attributes:
        enabled: Whether fault injection is active.
        num_faults: Number of faults to inject per run (1-5).
        categories: Which fault categories to include. Empty means all.
        severities: Which severity levels to include. Empty means all.
        exclude_faults: Fault IDs to exclude from selection.
        seed: Random seed for reproducible fault selection.
        backup_compose: Whether to back up compose files before injection.
        platform: Target platform for fault selection ("compose" or "k8s").
    """

    VALID_PLATFORMS = {"compose", "k8s"}

    enabled: bool = False
    num_faults: int = 2
    categories: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    exclude_faults: list[str] = field(default_factory=list)
    seed: int | None = None
    backup_compose: bool = True
    platform: str = "compose"

    def __post_init__(self):
        """Validate configuration after initialization."""
        validate_field(self.enabled, "enabled", bool)

        validate_type(self.num_faults, "num_faults", int)
        if self.num_faults < 1 or self.num_faults > 5:
            raise ValueError(f"num_faults must be between 1 and 5, got {self.num_faults}")

        validate_type(self.categories, "categories", list)
        for cat in self.categories:
            if cat not in _FAULT_VALID_CATEGORIES:
                raise ValueError(f"Invalid category '{cat}'. Valid categories: {sorted(_FAULT_VALID_CATEGORIES)}")

        validate_type(self.severities, "severities", list)
        for sev in self.severities:
            if sev not in _FAULT_VALID_SEVERITIES:
                raise ValueError(f"Invalid severity '{sev}'. Valid severities: {sorted(_FAULT_VALID_SEVERITIES)}")

        validate_field(self.exclude_faults, "exclude_faults", list)
        validate_field(self.seed, "seed", int, nullable=True)
        validate_field(self.backup_compose, "backup_compose", bool)
        validate_field(self.platform, "platform", str, valid_values=self.VALID_PLATFORMS)


@dataclass
class AgentConfig:
    provider: str = "codex"
    model: str | None = None
    location: str | None = None
    thinking_budget: int | None = None
    # Rate limiting and retry configuration
    max_retries: int = 3
    retry_base_delay: int = 5
    rate_limit_backoff: int = 60

    VALID_PROVIDERS = {
        "codex",
        "gemini",
        "claude",
        "claude-code",
        "opencode",
        "anthropic",
        "vertex",
        "openai",
        "rlm",
        "subagent",
        "hybrid",
    }

    def __post_init__(self):
        """Validate configuration values after initialization."""
        validate_field(self.provider, "provider", str)

        # Case-insensitive check
        if self.provider.lower() not in self.VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider: '{self.provider}'. Valid providers: {', '.join(sorted(self.VALID_PROVIDERS))}"
            )
        # Normalize provider name
        self.provider = self.provider.lower()

        validate_field(self.model, "model", str, nullable=True)
        validate_field(self.location, "location", str, nullable=True)

        if self.thinking_budget is not None:
            validate_field(self.thinking_budget, "thinking_budget", int, positive=True)

        # Validate retry configuration
        validate_field(self.max_retries, "max_retries", int, non_negative=True)
        validate_field(self.retry_base_delay, "retry_base_delay", int, positive=True)
        validate_field(self.rate_limit_backoff, "rate_limit_backoff", int, positive=True)


# Canonical mapping from SDS provider name to the litellm model prefix.
# Used wherever a litellm-compatible "prefix/model" string is needed.
PROVIDER_TO_LITELLM_PREFIX: dict[str, str] = {
    "gemini": "gemini",
    "vertex": "vertex_ai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "claude-code": "anthropic",
    "codex": "openai",
    "openai": "openai",
    "opencode": "openai",
    "rlm": "gemini",
}


def qualify_model_for_litellm(
    model: str,
    provider: str | None = None,
) -> str:
    """Return a fully-qualified ``provider/model`` string for litellm.

    Resolution order:
    1. If *model* already contains a ``/``, return it unchanged.
    2. If an SDS *provider* name is given, look it up in
       ``PROVIDER_TO_LITELLM_PREFIX``.
    3. Infer the prefix from well-known substrings in *model*.
    4. Fall back to *model* as-is.
    """
    if "/" in model:
        return model

    if provider is not None:
        prefix = PROVIDER_TO_LITELLM_PREFIX.get(provider)
        if prefix is not None:
            return f"{prefix}/{model}"

    # Heuristic: infer provider from the model name itself.
    lower = model.lower()
    if "claude" in lower:
        return f"anthropic/{model}"
    if "gpt" in lower or "o1" in lower:
        return f"openai/{model}"
    if "gemini" in lower:
        return f"gemini/{model}"

    return model


@dataclass
class DeploymentConfig:
    platform: str = "docker"
    target: str = "local"

    VALID_PLATFORMS = {"docker", "k8s"}
    VALID_TARGETS = {"local", "remote"}

    def __post_init__(self):
        """Validate configuration values after initialization."""
        validate_field(self.platform, "platform", str, valid_values=self.VALID_PLATFORMS)

        validate_type(self.target, "target", str)
        if self.target == "remote":
            raise ValueError("Remote deployment is not currently supported")
        if self.target not in self.VALID_TARGETS:
            raise ValueError(f"Invalid target: '{self.target}'. Valid targets: {', '.join(sorted(self.VALID_TARGETS))}")


@dataclass
class OperatorPhaseConfig:
    """Configuration for operator phase control."""

    code_analysis: bool = True
    fix_summary_consolidation: bool = True
    git_integration: bool = False

    def __post_init__(self):
        validate_field(self.code_analysis, "code_analysis", bool)
        validate_field(self.fix_summary_consolidation, "fix_summary_consolidation", bool)
        validate_field(self.git_integration, "git_integration", bool)


@dataclass
class OperatorConfig:
    interval: int = 30
    monitoring_max_iters: int = 5
    deployment_max_iters: int = 20
    agent_fix_timeout: int = 2700
    deploy_timeout: int = 900
    dynamic_observability_injection: bool = False
    agent_timeout: int = 900
    phase: OperatorPhaseConfig = field(default_factory=OperatorPhaseConfig)

    def __post_init__(self):
        """Validate configuration values after initialization."""
        validate_field(self.interval, "interval", int, positive=True)
        if self.interval > 86400:
            raise ValueError(f"interval too large: {self.interval}s (max: 86400s/24h)")

        for field_name in [
            "monitoring_max_iters",
            "deployment_max_iters",
            "agent_fix_timeout",
            "deploy_timeout",
            "agent_timeout",
        ]:
            value = getattr(self, field_name)
            validate_field(value, field_name, int, positive=True)


@dataclass
class RuntimeConfig:
    impl: str = "cli_agent"

    VALID_IMPLS = {"cli_agent", "langgraph", "adk"}

    def __post_init__(self):
        validate_field(self.impl, "impl", str, valid_values=self.VALID_IMPLS)


@dataclass
class GEPAConfig:
    """Configuration for GEPA prompt optimization.

    Added to sds.toml under [gepa] section:

    [gepa]
    max_steps = 50
    num_candidates = 10
    minibatch_size = 3
    reflection_provider = "gemini"
    reflection_model = "gemini-2.5-pro"
    output_dir = "gepa_runs"
    """

    max_steps: int = 50
    num_candidates: int = 10
    minibatch_size: int = 3
    validation_size: int = 10
    mutation_probability: float = 0.7
    diversity_probability: float = 0.1
    patience: int = 10
    checkpoint_interval: int = 5
    reflection_provider: str = "gemini"
    reflection_model: str | None = "gemini-2.5-pro"
    seed: int | None = None
    output_dir: str = "gepa_runs"

    def __post_init__(self):
        """Validate configuration values after initialization."""
        for field_name in [
            "max_steps",
            "num_candidates",
            "minibatch_size",
            "validation_size",
            "patience",
            "checkpoint_interval",
        ]:
            validate_field(getattr(self, field_name), field_name, int, positive=True)

        for prob_field in ["mutation_probability", "diversity_probability"]:
            validate_type(
                getattr(self, prob_field),
                prob_field,
                (int, float),
                type_label=_NUMERIC_LABEL,
            )
            validate_range(
                getattr(self, prob_field),
                prob_field,
                min_val=0.0,
                max_val=1.0,
            )

        validate_field(self.seed, "seed", int, nullable=True)

        validate_field(self.reflection_provider, "reflection_provider", str)
        self.reflection_provider = self.reflection_provider.lower()
        if self.reflection_provider not in AgentConfig.VALID_PROVIDERS:
            raise ValueError(
                f"Invalid reflection_provider: '{self.reflection_provider}'. "
                f"Valid providers: {', '.join(sorted(AgentConfig.VALID_PROVIDERS))}"
            )

        validate_field(self.reflection_model, "reflection_model", str, nullable=True)
        validate_field(self.output_dir, "output_dir", str)


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    gepa: GEPAConfig = field(default_factory=GEPAConfig)
    dspy: DSPyConfig = field(default_factory=DSPyConfig)
    fault_injection: FaultInjectionConfig = field(default_factory=FaultInjectionConfig)

    @staticmethod
    def _validate_fields(section_data: dict, section_name: str, config_class: type) -> None:
        """Validate that all fields in a section are recognized."""
        validate_dataclass_fields(section_data, section_name, config_class)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        # Derive recognized sections from Config's own dataclass fields
        recognized_sections = {f.name for f in fields(cls)}
        unrecognized_sections = set(data.keys()) - recognized_sections
        if unrecognized_sections:
            raise UnrecognizedSectionError(
                f"Unrecognized section(s) in config: "
                f"{', '.join(sorted(unrecognized_sections))}. "
                f"Recognized sections are: {', '.join(sorted(recognized_sections))}"
            )

        # Extract and validate section data
        agent_data = data.get("agent", {})
        operator_data = data.get("operator", {})
        deployment_data = data.get("deployment", {})
        runtime_data = data.get("runtime", {})
        gepa_data = data.get("gepa", {})
        dspy_data = data.get("dspy", {})
        fault_injection_data = data.get("fault_injection", {})

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)
        cls._validate_operator_phase_fields(operator_data)
        cls._validate_fields(deployment_data, "deployment", DeploymentConfig)
        cls._validate_fields(runtime_data, "runtime", RuntimeConfig)
        cls._validate_fields(gepa_data, "gepa", GEPAConfig)
        cls._validate_dspy_fields(dspy_data)
        cls._validate_fields(fault_injection_data, "fault_injection", FaultInjectionConfig)

        # Create agent config first to access model info
        agent_config = AgentConfig(**agent_data)

        # Parse DSPy config and auto-populate runtime_model if not set
        dspy_config = cls._parse_dspy_config(dspy_data, agent_config)

        return cls(
            agent=agent_config,
            operator=cls._parse_operator_config(operator_data),
            deployment=DeploymentConfig(**deployment_data),
            runtime=RuntimeConfig(**runtime_data),
            gepa=GEPAConfig(**gepa_data),
            dspy=dspy_config,
            fault_injection=FaultInjectionConfig(**fault_injection_data),
        )

    @classmethod
    def _validate_dspy_fields(cls, dspy_data: dict) -> None:
        """Validate DSPy configuration fields."""
        if not dspy_data:
            return

        # Top-level DSPy fields
        validate_dataclass_fields(dspy_data, "dspy", DSPyConfig)

        # Validate nested optimization section
        if "optimization" in dspy_data:
            opt_data = dspy_data["optimization"]
            if isinstance(opt_data, dict):
                validate_dataclass_fields(opt_data, "dspy.optimization", DSPyOptimizationConfig)

        # Validate nested auto_rollback section
        if "auto_rollback" in dspy_data:
            rollback_data = dspy_data["auto_rollback"]
            if isinstance(rollback_data, dict):
                validate_dataclass_fields(rollback_data, "dspy.auto_rollback", DSPyAutoRollbackConfig)

    @classmethod
    def _validate_operator_phase_fields(cls, operator_data: dict) -> None:
        """Validate operator.phase configuration fields."""
        if not operator_data or "phase" not in operator_data:
            return

        phase_data = operator_data["phase"]
        if not isinstance(phase_data, dict):
            return

        validate_dataclass_fields(phase_data, "operator.phase", OperatorPhaseConfig)

    @classmethod
    def _parse_dspy_config(cls, dspy_data: dict, agent_config: AgentConfig) -> DSPyConfig:
        """Parse DSPy configuration with nested sections.

        Args:
            dspy_data: DSPy configuration data from TOML
            agent_config: Agent configuration (used to auto-populate runtime_model)

        Returns:
            Parsed DSPy configuration
        """
        if not dspy_data:
            return DSPyConfig()

        # Make a copy to avoid modifying the input
        dspy_data = dict(dspy_data)

        # Extract nested sections
        optimization_data = dspy_data.pop("optimization", {})
        auto_rollback_data = dspy_data.pop("auto_rollback", {})

        # Auto-populate runtime_model if not explicitly set.
        #
        # The [agent] model is consumed by the CLI coding agent (e.g. the
        # Gemini CLI) and may not be a valid litellm model string.  For
        # example, "gemini-3-pro-preview" works via the CLI but does not
        # exist as a Vertex AI publisher model.
        #
        # teacher_model, on the other hand, is already a fully-qualified
        # litellm model string (e.g. "vertex_ai/gemini-2.5-pro") that has
        # been validated during optimization.  Use it as the default when
        # available; fall back to mapping [agent] provider/model only when
        # no teacher_model is configured.
        if "runtime_model" not in dspy_data:
            teacher_model = optimization_data.get("teacher_model", "")
            if "/" in teacher_model:
                # teacher_model is already a qualified litellm string
                dspy_data["runtime_model"] = teacher_model
            elif agent_config.model:
                # No teacher_model available; derive from agent config
                provider = agent_config.provider
                model = agent_config.model

                dspy_data["runtime_model"] = qualify_model_for_litellm(model, provider=provider)

        # Create nested config objects
        optimization = DSPyOptimizationConfig(**optimization_data) if optimization_data else DSPyOptimizationConfig()
        auto_rollback = DSPyAutoRollbackConfig(**auto_rollback_data) if auto_rollback_data else DSPyAutoRollbackConfig()

        # Propagate agent location to DSPy runtime if not already set
        if "vertex_location" not in dspy_data and agent_config.location:
            dspy_data["vertex_location"] = agent_config.location

        # Create main DSPy config with nested objects
        return DSPyConfig(
            **dspy_data,
            optimization=optimization,
            auto_rollback=auto_rollback,
        )

    @classmethod
    def _parse_operator_config(cls, operator_data: dict) -> OperatorConfig:
        """Parse Operator configuration with nested phase section."""
        if not operator_data:
            return OperatorConfig()

        operator_data = dict(operator_data)  # Copy to avoid mutation
        phase_data = operator_data.pop("phase", {})

        phase = OperatorPhaseConfig(**phase_data) if phase_data else OperatorPhaseConfig()

        return OperatorConfig(**operator_data, phase=phase)


def _deep_merge(base: dict, update: dict) -> dict:
    """Recursively merge update dict into base dict."""
    for k, v in update.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(target_dir: str, config_path: str | None = None) -> Config:
    """Load configuration from sds.toml or config.toml.

    Args:
        target_dir: Directory to look for configuration files.
        config_path: Optional explicit path to configuration file.

    Returns:
        Config: The loaded configuration object.
    """
    target_path = Path(target_dir)
    merged_data = {}

    if config_path:
        # If explicit path provided, load only that
        files_to_load = [(Path(config_path), False)]  # (path, is_app_config)
    else:
        # Determine project root (where this package is installed/located)
        project_root = Path(__file__).resolve().parent.parent

        # Define hierarchy: Repo root -> Target dir -> App .sds override
        # Each layer merges on top of the previous.

        files_to_load = []

        # 1. Always load repo root sds.toml as the base (if it exists and differs from target)
        root_sds = project_root / "sds.toml"
        if root_sds.exists() and root_sds.resolve() != (target_path / "sds.toml").resolve():
            files_to_load.append((root_sds, False))

        # 2. Load target dir config on top (overrides root)
        for f in [target_path / "sds.toml", target_path / "config.toml"]:
            if f.exists():
                files_to_load.append((f, False))
                break

        # 3. Load app .sds config on top (overrides target)
        for f in [target_path / ".sds" / "config.toml", target_path / ".sds" / "sds.toml"]:
            if f.exists():
                files_to_load.append((f, True))
                break

    for config_file, is_app_config in files_to_load:
        if not config_file.exists():
            continue

        try:
            with open(config_file, "rb") as f:
                data = tomllib.load(f)
                logger.info(f"Loaded configuration from {config_file}")

                if is_app_config:
                    # Validate app config only contains deployment settings
                    forbidden_sections = set(data.keys()) - {"deployment"}
                    if forbidden_sections:
                        raise UnrecognizedSectionError(
                            f"Application config {config_file} may only contain "
                            f"[deployment] section. Found forbidden section(s): "
                            f"{', '.join(sorted(forbidden_sections))}"
                        )

                _deep_merge(merged_data, data)

        except (ConfigurationError, TypeError):
            # Re-raise config validation errors and TypeError from dataclass
            raise
        except Exception as e:
            # Re-raise parsing errors to prevent silent fallback to defaults
            raise ConfigurationError(f"Failed to parse {config_file}: {e}") from e

    return Config.from_dict(merged_data)
