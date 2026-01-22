try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, fields
import sys


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


@dataclass
class OperatorConfig:
    interval: int = 30
    monitoring_max_iters: int = 5
    deployment_max_iters: int = 5


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)

    @staticmethod
    def _validate_fields(
        section_data: dict,
        section_name: str,
        config_class: type
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
        recognized_sections = {"agent", "operator"}
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

        cls._validate_fields(agent_data, "agent", AgentConfig)
        cls._validate_fields(operator_data, "operator", OperatorConfig)

        return cls(
            agent=AgentConfig(**agent_data),
            operator=OperatorConfig(**operator_data)
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
            target_path / "sds.toml",
            target_path / "config.toml",
            project_root / "sds.toml"
        ]

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    data = tomllib.load(f)
                    print(f"Loaded configuration from {config_file}")
                    return Config.from_dict(data)
            except (ConfigError, TypeError):
                # Re-raise config validation errors and TypeError from dataclass
                raise
            except Exception as e:
                print(
                    f"Warning: Failed to parse {config_file}: {e}",
                    file=sys.stderr)

    return Config()
