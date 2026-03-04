"""Minimal configuration for lego_agent.

Only parses [agent] and [operator] sections from sds.toml.
App_operator-specific sections (dspy, fault_injection, deployment, runtime)
are silently ignored.
"""

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    provider: str = "codex"
    model: str | None = None
    location: str | None = None
    thinking_budget: int | None = None
    max_retries: int = 3
    retry_base_delay: int = 5
    rate_limit_backoff: int = 60

    def __post_init__(self):
        if not isinstance(self.provider, str):
            raise TypeError(f"provider must be a str, got {type(self.provider)}")
        self.provider = self.provider.lower()
        if self.model is not None and not isinstance(self.model, str):
            raise TypeError(f"model must be a str or None, got {type(self.model)}")
        if self.thinking_budget is not None and not isinstance(self.thinking_budget, int):
            raise TypeError(
                f"thinking_budget must be an int or None, got {type(self.thinking_budget)}"
            )


@dataclass
class OperatorConfig:
    agent_timeout: int = 900

    def __post_init__(self):
        if not isinstance(self.agent_timeout, int):
            raise TypeError(
                f"agent_timeout must be an int, got {type(self.agent_timeout)}"
            )


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        agent_data = data.get("agent", {})
        operator_data = data.get("operator", {})

        # Only pass recognised fields to OperatorConfig
        operator_kwargs = {}
        if "agent_timeout" in operator_data:
            operator_kwargs["agent_timeout"] = operator_data["agent_timeout"]

        # Only pass recognised fields to AgentConfig
        agent_field_names = {
            "provider", "model", "location", "thinking_budget",
            "max_retries", "retry_base_delay", "rate_limit_backoff",
        }
        agent_kwargs = {k: v for k, v in agent_data.items() if k in agent_field_names}

        return cls(
            agent=AgentConfig(**agent_kwargs),
            operator=OperatorConfig(**operator_kwargs),
        )


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
    merged_data: dict = {}

    if config_path:
        files_to_load = [Path(config_path)]
    else:
        project_root = Path(__file__).resolve().parent.parent

        files_to_load = []

        root_sds = project_root / "sds.toml"
        if root_sds.exists() and root_sds.resolve() != (target_path / "sds.toml").resolve():
            files_to_load.append(root_sds)

        for f in [target_path / "sds.toml", target_path / "config.toml"]:
            if f.exists():
                files_to_load.append(f)
                break

        for f in [target_path / ".sds" / "config.toml", target_path / ".sds" / "sds.toml"]:
            if f.exists():
                files_to_load.append(f)
                break

    for config_file in files_to_load:
        if not config_file.exists():
            continue
        with open(config_file, "rb") as f:
            data = tomllib.load(f)
        _deep_merge(merged_data, data)

    return Config.from_dict(merged_data)
