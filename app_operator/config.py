try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, fields

from app_operator.logger import logger


class ConfigError(Exception):
    """Base exception for configuration errors."""

    pass


class UnrecognizedSectionError(ConfigError):
    """Raised when an unrecognized section is found in the config file."""

    pass


class UnrecognizedFieldError(ConfigError):
    """Raised when an unrecognized field is found in a recognized section."""

    pass


@dataclass
class AgentConfig:
    provider: str = "codex"
    model: Optional[str] = None

    VALID_PROVIDERS = {"codex", "gemini", "claude", "claude-code", "opencode"}

    def __post_init__(self):
        """Validate configuration values after initialization."""
        if not isinstance(self.provider, str):
            raise TypeError(f"provider must be str, got {type(self.provider).__name__}")
        if self.provider.lower() not in self.VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider: '{self.provider}'. "
                f"Valid providers: {', '.join(sorted(self.VALID_PROVIDERS))}"
            )
        if self.model is not None and not isinstance(self.model, str):
            raise TypeError(
                f"model must be str or None, got {type(self.model).__name__}"
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

    def __post_init__(self):
        """Validate configuration values after initialization."""
        if not isinstance(self.interval, int):
            raise TypeError(f"interval must be int, got {type(self.interval).__name__}")
        if self.interval <= 0:
            raise ValueError(f"interval must be positive, got {self.interval}")
        if self.interval > 86400:
            raise ValueError(f"interval too large: {self.interval}s (max: 86400s/24h)")

        for field_name in ["monitoring_max_iters", "deployment_max_iters"]:
            value = getattr(self, field_name)
            if not isinstance(value, int):
                raise TypeError(f"{field_name} must be int, got {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field_name} must be positive, got {value}")


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)

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
        recognized_sections = {"agent", "operator", "deployment"}
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

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)
        cls._validate_fields(deployment_data, "deployment", DeploymentConfig)

        return cls(
            agent=AgentConfig(**agent_data),
            operator=OperatorConfig(**operator_data),
            deployment=DeploymentConfig(**deployment_data),
        )


def load_config(target_dir: str, config_path: Optional[str] = None) -> Config:
    """Load configuration from sds.toml or config.toml.

    Args:
        target_dir: Directory to look for configuration files.
        config_path: Optional explicit path to configuration file.

    Returns:
        Config: The loaded configuration object.
    """
    target_path = Path(target_dir)

    if config_path:
        config_files = [Path(config_path)]
    else:
        # Determine project root (where this package is installed/located)
        project_root = Path(__file__).resolve().parent.parent
        config_files = [
            target_path / ".sds" / "config.toml",
            target_path / ".sds" / "sds.toml",
            target_path / "sds.toml",
            target_path / "config.toml",
            project_root / "sds.toml",
        ]

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    data = tomllib.load(f)
                    logger.info(f"Loaded configuration from {config_file}")
                    return Config.from_dict(data)
            except (ConfigError, TypeError):
                # Re-raise config validation errors and TypeError from dataclass
                raise
            except Exception as e:
                logger.warning(f"Warning: Failed to parse {config_file}: {e}")

    return Config()
