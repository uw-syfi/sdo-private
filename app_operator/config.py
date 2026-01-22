try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
import sys


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

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        agent_data = data.get("agent", {})
        operator_data = data.get("operator", {})

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
            except Exception as e:
                print(
                    f"Warning: Failed to parse {config_file}: {e}",
                    file=sys.stderr)

    return Config()
