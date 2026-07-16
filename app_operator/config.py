from __future__ import annotations

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
    validate_type,
)
from libs.model_config import ModelConfig, from_provider_and_model


class UnrecognizedSectionError(ConfigurationError):
    """Raised when an unrecognized section is found in the config file."""


class UnrecognizedFieldError(ConfigurationError):
    """Raised when an unrecognized field is found in a recognized section."""


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
    backend: str = "codex"
    # Rate limiting and retry configuration
    max_retries: int = 3
    retry_base_delay: int = 5
    rate_limit_backoff: int = 60
    step_limit: int | None = 1000  # hard limit; soft limit = max(0, step_limit - 5)
    model_config: ModelConfig | None = None

    # "rlm" is intentionally absent: RLMAgent is only reachable via backend="hybrid".
    VALID_BACKENDS = {
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
        validate_field(self.backend, "backend", str)

        # Case-insensitive check
        if self.backend.lower() not in self.VALID_BACKENDS:
            raise ValueError(
                f"Invalid backend/provider: '{self.backend}'. Invalid provider alias. "
                f"Valid providers/backends: {', '.join(sorted(self.VALID_BACKENDS))}"
            )
        # Normalize backend name
        self.backend = self.backend.lower()

        # Validate retry configuration
        validate_field(self.max_retries, "max_retries", int, non_negative=True)
        validate_field(self.retry_base_delay, "retry_base_delay", int, positive=True)
        validate_field(self.rate_limit_backoff, "rate_limit_backoff", int, positive=True)

        if self.model_config is not None and not isinstance(self.model_config, ModelConfig):
            raise TypeError(f"model_config must be a ModelConfig or None, got {type(self.model_config).__name__}")

    @property
    def model(self) -> str | None:
        return self.model_config.model if self.model_config else None

    @model.setter
    def model(self, value: str | None) -> None:
        """Rebuild model_config preserving location and thinking_budget."""
        if value is None:
            self.model_config = None
            return
        loc = self.model_config.location if self.model_config else None
        tb = self.model_config.thinking_budget if self.model_config else None
        _UNRESOLVABLE = {"subagent", "hybrid"}
        if self.backend not in _UNRESOLVABLE:
            try:
                self.model_config = from_provider_and_model(self.backend, value, location=loc, thinking_budget=tb)
            except ValueError:
                self.model_config = ModelConfig.from_string(
                    value,
                    provider_hint=self.backend,
                    location=loc,
                    thinking_budget=tb,
                )
        else:
            self.model_config = ModelConfig.from_string(value, location=loc, thinking_budget=tb)

    @property
    def location(self) -> str | None:
        return self.model_config.location if self.model_config else None

    @property
    def thinking_budget(self) -> int | None:
        return self.model_config.thinking_budget if self.model_config else None


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

    Delegates to ``ModelConfig.from_string`` for provider resolution.
    """
    from libs.model_config import from_string

    return from_string(model, provider_hint=provider).to_litellm_str()


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
class FeaturesConfig:
    """Configuration for cross-cutting capability flags."""

    git_integration: bool = False

    def __post_init__(self):
        validate_field(self.git_integration, "git_integration", bool)


@dataclass
class OperatorPhaseConfig:
    """Configuration for operator phase control."""

    code_analysis: bool = True
    fix_summary_consolidation: bool = (
        True  # when True, agents maintain deployment_progress.md to prevent re-trying refuted hypotheses
    )
    health_monitoring: bool = True

    def __post_init__(self):
        validate_field(self.code_analysis, "code_analysis", bool)
        validate_field(self.fix_summary_consolidation, "fix_summary_consolidation", bool)
        validate_field(self.health_monitoring, "health_monitoring", bool)


@dataclass
class OperatorConfig:
    interval: int = 30
    monitoring_max_iters: int = 5
    deployment_max_iters: int = 20
    agent_fix_timeout: int = 2700
    deploy_timeout: int = 900
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

    VALID_IMPLS = {"cli_agent", "pydantic_ai"}

    def __post_init__(self):
        validate_field(self.impl, "impl", str, valid_values=self.VALID_IMPLS)


@dataclass
class RLMConfig:
    """Configuration for the CLI-agent RLM scaffold."""

    VALID_MODES = {"compatibility", "paper_faithful"}

    mode: str = "compatibility"

    def __post_init__(self):
        validate_field(self.mode, "mode", str)
        if self.mode not in self.VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(self.VALID_MODES)}, got '{self.mode}'")


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    rlm: RLMConfig = field(default_factory=RLMConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    fault_injection: FaultInjectionConfig = field(default_factory=FaultInjectionConfig)

    @staticmethod
    def _validate_fields(section_data: dict, section_name: str, config_class: type) -> None:
        """Validate that all fields in a section are recognized."""
        validate_dataclass_fields(section_data, section_name, config_class)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
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
        agent_data = dict(data.get("agent", {}))
        operator_data = data.get("operator", {})
        deployment_data = data.get("deployment", {})
        runtime_data = data.get("runtime", {})
        rlm_data = data.get("rlm", {})
        features_data = data.get("features", {})
        fault_injection_data = data.get("fault_injection", {})

        # Pop model-related flat keys - these are not AgentConfig fields but are
        # accepted in TOML for convenience and used to build model_config.
        _raw_provider = agent_data.pop("provider", None)
        _raw_model = agent_data.pop("model", None)
        _raw_location = agent_data.pop("location", None)
        _raw_thinking_budget = agent_data.pop("thinking_budget", None)
        if _raw_provider is not None:
            agent_data["backend"] = _raw_provider

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)
        cls._validate_operator_phase_fields(operator_data)
        cls._validate_fields(deployment_data, "deployment", DeploymentConfig)
        cls._validate_fields(runtime_data, "runtime", RuntimeConfig)
        cls._validate_fields(rlm_data, "rlm", RLMConfig)
        cls._validate_fields(features_data, "features", FeaturesConfig)
        cls._validate_fields(fault_injection_data, "fault_injection", FaultInjectionConfig)

        # Build model_config from flat keys
        _raw_backend = agent_data.get("backend", "codex").lower()
        _UNRESOLVABLE = {"subagent", "hybrid"}
        _agent_model_config = None
        if _raw_model:
            if _raw_backend not in _UNRESOLVABLE:
                try:
                    _agent_model_config = from_provider_and_model(
                        _raw_backend,
                        _raw_model,
                        location=_raw_location,
                        thinking_budget=_raw_thinking_budget,
                    )
                except ValueError:
                    pass
            else:
                _agent_model_config = ModelConfig.from_string(
                    _raw_model, location=_raw_location, thinking_budget=_raw_thinking_budget
                )

        agent_config = AgentConfig(**agent_data, model_config=_agent_model_config)

        runtime_config = RuntimeConfig(**runtime_data)
        if (
            runtime_config.impl == "cli_agent"
            and "agent" in data
            and agent_config.backend != "codex"
            and not agent_config.model
        ):
            raise ValueError("agent.model is required for cli_agent runtime when agent.backend is set")
        return cls(
            agent=agent_config,
            operator=cls._parse_operator_config(operator_data),
            deployment=DeploymentConfig(**deployment_data),
            runtime=runtime_config,
            rlm=RLMConfig(**rlm_data),
            features=FeaturesConfig(**features_data),
            fault_injection=FaultInjectionConfig(**fault_injection_data),
        )

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
        except (OSError, ValueError, KeyError) as e:
            # Re-raise parsing errors to prevent silent fallback to defaults
            raise ConfigurationError(f"Failed to parse {config_file}: {e}") from e

    return Config.from_dict(merged_data)
