try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, fields

from app_operator.logger import logger
from app_operator.exceptions import ConfigurationError
from app_operator.dspy_integration.config import DSPyConfig
from app_operator.fault_injection.config import FaultInjectionConfig


class UnrecognizedSectionError(ConfigurationError):
    """Raised when an unrecognized section is found in the config file."""

    pass


class UnrecognizedFieldError(ConfigurationError):
    """Raised when an unrecognized field is found in a recognized section."""

    pass


@dataclass
class AgentConfig:
    provider: str = "codex"
    model: Optional[str] = None
    location: Optional[str] = None

    VALID_PROVIDERS = {
        "codex",
        "gemini",
        "claude",
        "claude-code",
        "opencode",
        "anthropic",
        "vertex",
        "openai",
    }

    def __post_init__(self):
        """Validate configuration values after initialization."""
        if not isinstance(self.provider, str):
            raise TypeError(f"provider must be str, got {type(self.provider).__name__}")

        # Case-insensitive check
        if self.provider.lower() not in self.VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider: '{self.provider}'. "
                f"Valid providers: {', '.join(sorted(self.VALID_PROVIDERS))}"
            )
        # Normalize provider name
        self.provider = self.provider.lower()

        if self.model is not None and not isinstance(self.model, str):
            raise TypeError(
                f"model must be str or None, got {type(self.model).__name__}"
            )
        if self.location is not None and not isinstance(self.location, str):
            raise TypeError(
                f"location must be str or None, got {type(self.location).__name__}"
            )


@dataclass
class DeploymentConfig:
    platform: str = "docker"
    target: str = "local"

    VALID_PLATFORMS = {"docker", "k8s"}
    VALID_TARGETS = {"local", "remote"}

    def __post_init__(self):
        """Validate configuration values after initialization."""
        if not isinstance(self.platform, str):
            raise TypeError(f"platform must be str, got {type(self.platform).__name__}")
        if self.platform not in self.VALID_PLATFORMS:
            raise ValueError(
                f"Invalid platform: '{self.platform}'. "
                f"Valid platforms: {', '.join(sorted(self.VALID_PLATFORMS))}"
            )

        if not isinstance(self.target, str):
            raise TypeError(f"target must be str, got {type(self.target).__name__}")
        if self.target == "remote":
            raise ValueError("Remote deployment is not currently supported")
        if self.target not in self.VALID_TARGETS:
            raise ValueError(
                f"Invalid target: '{self.target}'. "
                f"Valid targets: {', '.join(sorted(self.VALID_TARGETS))}"
            )


@dataclass
class OperatorConfig:
    interval: int = 30
    monitoring_max_iters: int = 5
    deployment_max_iters: int = 5
    agent_fix_timeout: int = 1800
    deploy_timeout: int = 900
    agent_timeout: int = 300

    def __post_init__(self):
        """Validate configuration values after initialization."""
        if not isinstance(self.interval, int):
            raise TypeError(f"interval must be int, got {type(self.interval).__name__}")
        if self.interval <= 0:
            raise ValueError(f"interval must be positive, got {self.interval}")
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
            if not isinstance(value, int):
                raise TypeError(f"{field_name} must be int, got {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field_name} must be positive, got {value}")


@dataclass
class RuntimeConfig:
    impl: str = "cli_agent"

    VALID_IMPLS = {"cli_agent", "langgraph", "adk"}

    def __post_init__(self):
        if not isinstance(self.impl, str):
            raise TypeError(f"impl must be str, got {type(self.impl).__name__}")
        if self.impl not in self.VALID_IMPLS:
            raise ValueError(
                f"Invalid impl: '{self.impl}'. "
                f"Valid impls: {', '.join(sorted(self.VALID_IMPLS))}"
            )


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    dspy: DSPyConfig = field(default_factory=DSPyConfig)
    fault_injection: FaultInjectionConfig = field(default_factory=FaultInjectionConfig)

    def __post_init__(self):
        self._validate_runtime_requirements()

    def _validate_runtime_requirements(self) -> None:
        """Validate requirements for specific runtimes."""
        if self.runtime.impl == "langgraph":
            if not self.agent.provider:
                raise ValueError("agent.provider must be set for langgraph runtime")
            if not self.agent.model:
                raise ValueError("agent.model must be set for langgraph runtime")
        elif self.runtime.impl == "adk":
            if not self.agent.model:
                raise ValueError("agent.model must be set for adk runtime")

    @staticmethod
    def _validate_fields(
        section_data: dict, section_name: str, config_class: type
    ) -> None:
        """Validate that all fields in a section are recognized."""
        if not section_data:
            return

        recognized_fields = {f.name for f in fields(config_class)}
        unrecognized_fields = set(section_data.keys()) - recognized_fields

        if unrecognized_fields:
            raise UnrecognizedFieldError(
                f"Unrecognized field(s) in [{section_name}] section: "
                f"{', '.join(sorted(unrecognized_fields))}. "
                f"Recognized fields are: {', '.join(sorted(recognized_fields))}"
            )

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        # Validate top-level sections
        recognized_sections = {
            "agent",
            "operator",
            "deployment",
            "runtime",
            "dspy",
            "fault_injection"}
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
        dspy_data = data.get("dspy", {})
        fault_injection_data = data.get("fault_injection", {})

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)
        cls._validate_fields(deployment_data, "deployment", DeploymentConfig)
        cls._validate_fields(runtime_data, "runtime", RuntimeConfig)
        cls._validate_dspy_fields(dspy_data)
        cls._validate_fields(fault_injection_data, "fault_injection", FaultInjectionConfig)

        # Create agent config first to access model info
        agent_config = AgentConfig(**agent_data)

        # Parse DSPy config and auto-populate runtime_model if not set
        dspy_config = cls._parse_dspy_config(dspy_data, agent_config)

        return cls(
            agent=agent_config,
            operator=OperatorConfig(**operator_data),
            deployment=DeploymentConfig(**deployment_data),
            runtime=RuntimeConfig(**runtime_data),
            dspy=dspy_config,
            fault_injection=FaultInjectionConfig(**fault_injection_data),
        )

    @classmethod
    def _validate_dspy_fields(cls, dspy_data: dict) -> None:
        """Validate DSPy configuration fields."""
        if not dspy_data:
            return

        from app_operator.dspy_integration.config import (
            DSPyConfig,
            DSPyOptimizationConfig,
            DSPyAutoRollbackConfig,
        )

        # Top-level DSPy fields
        recognized_top_level = {f.name for f in fields(DSPyConfig)}
        unrecognized_top_level = set(dspy_data.keys()) - recognized_top_level
        if unrecognized_top_level:
            raise UnrecognizedFieldError(
                f"Unrecognized field(s) in [dspy] section: "
                f"{', '.join(sorted(unrecognized_top_level))}. "
                f"Recognized fields are: {', '.join(sorted(recognized_top_level))}"
            )

        # Validate nested optimization section
        if "optimization" in dspy_data:
            opt_data = dspy_data["optimization"]
            if isinstance(opt_data, dict):
                recognized_opt = {f.name for f in fields(DSPyOptimizationConfig)}
                unrecognized_opt = set(opt_data.keys()) - recognized_opt
                if unrecognized_opt:
                    raise UnrecognizedFieldError(
                        f"Unrecognized field(s) in [dspy.optimization] section: "
                        f"{', '.join(sorted(unrecognized_opt))}. "
                        f"Recognized fields are: {', '.join(sorted(recognized_opt))}"
                    )

        # Validate nested auto_rollback section
        if "auto_rollback" in dspy_data:
            rollback_data = dspy_data["auto_rollback"]
            if isinstance(rollback_data, dict):
                recognized_rollback = {f.name for f in fields(DSPyAutoRollbackConfig)}
                unrecognized_rollback = set(rollback_data.keys()) - recognized_rollback
                if unrecognized_rollback:
                    raise UnrecognizedFieldError(
                        f"Unrecognized field(s) in [dspy.auto_rollback] section: "
                        f"{', '.join(sorted(unrecognized_rollback))}. "
                        f"Recognized fields are: {', '.join(sorted(recognized_rollback))}"
                    )

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

        from app_operator.dspy_integration.config import (
            DSPyOptimizationConfig,
            DSPyAutoRollbackConfig,
        )

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

                provider_mapping = {
                    "gemini": "gemini",
                    "vertex": "vertex_ai",
                    "claude": "anthropic",
                    "anthropic": "anthropic",
                    "codex": "openai",
                    "openai": "openai",
                }
                dspy_provider = provider_mapping.get(provider, provider)
                dspy_data["runtime_model"] = f"{dspy_provider}/{model}"

        # Create nested config objects
        optimization = DSPyOptimizationConfig(
            **optimization_data) if optimization_data else DSPyOptimizationConfig()
        auto_rollback = DSPyAutoRollbackConfig(
            **auto_rollback_data) if auto_rollback_data else DSPyAutoRollbackConfig()

        # Create main DSPy config with nested objects
        return DSPyConfig(
            **dspy_data,
            optimization=optimization,
            auto_rollback=auto_rollback,
        )


def _deep_merge(base: dict, update: dict) -> dict:
    """Recursively merge update dict into base dict."""
    for k, v in update.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(target_dir: str, config_path: Optional[str] = None) -> Config:
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

        # Define hierarchy: Base (Repo/Root) -> Override (App .sds)
        # We load base first, then merge override on top.

        # Potential base config files (pick first that exists)
        base_candidates = [
            target_path / "sds.toml",
            target_path / "config.toml",
            project_root / "sds.toml",
        ]

        # Potential app config files (pick first that exists)
        app_candidates = [
            target_path / ".sds" / "config.toml",
            target_path / ".sds" / "sds.toml",
        ]

        files_to_load = []

        # Find base config
        for f in base_candidates:
            if f.exists():
                files_to_load.append((f, False))
                break

        # Find app config
        for f in app_candidates:
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
