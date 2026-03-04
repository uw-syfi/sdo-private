try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, fields

from app_operator.logger import logger
from app_operator.exceptions import ConfigurationError


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
    thinking_budget: Optional[int] = None

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
        if self.thinking_budget is not None:
            if not isinstance(self.thinking_budget, int):
                raise TypeError(
                    f"thinking_budget must be int or None, got {type(self.thinking_budget).__name__}"
                )
            if self.thinking_budget <= 0:
                raise ValueError(
                    f"thinking_budget must be positive, got {self.thinking_budget}"
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
    dynamic_observability_injection: bool = False

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
        recognized_sections = {"agent", "operator", "deployment", "runtime"}
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

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)
        cls._validate_fields(deployment_data, "deployment", DeploymentConfig)
        cls._validate_fields(runtime_data, "runtime", RuntimeConfig)

        return cls(
            agent=AgentConfig(**agent_data),
            operator=OperatorConfig(**operator_data),
            deployment=DeploymentConfig(**deployment_data),
            runtime=RuntimeConfig(**runtime_data),
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
