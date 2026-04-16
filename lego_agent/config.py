"""Minimal configuration for lego_agent.

Only parses [agent] and [operator] sections from sds.toml.
App_operator-specific sections (dspy, fault_injection, deployment, runtime)
are silently ignored.
"""

from __future__ import annotations

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[reportMissingImports]

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from libs.model_config import ModelConfig, from_provider_and_model


@dataclass
class AgentConfig:
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

    backend: str = "codex"
    max_retries: int = 3
    retry_base_delay: int = 5
    rate_limit_backoff: int = 60
    model_config: ModelConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"backend must be a str, got {type(self.backend)}")
        self.backend = self.backend.lower()
        if self.backend not in self.VALID_BACKENDS:
            raise ValueError(
                f"Invalid backend: '{self.backend}'. Valid backends: {', '.join(sorted(self.VALID_BACKENDS))}"
            )

        if self.model_config is not None and not isinstance(self.model_config, ModelConfig):  # pyright: ignore[reportUnnecessaryIsInstance]
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
                pass
        else:
            self.model_config = ModelConfig.from_string(value, location=loc, thinking_budget=tb)

    @property
    def location(self) -> str | None:
        return self.model_config.location if self.model_config else None

    @property
    def thinking_budget(self) -> int | None:
        return self.model_config.thinking_budget if self.model_config else None


@dataclass
class OperatorConfig:
    agent_timeout: int = 900

    def __post_init__(self) -> None:
        if not isinstance(self.agent_timeout, int):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"agent_timeout must be an int, got {type(self.agent_timeout)}")


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        agent_data: dict[str, Any] = dict(data.get("agent", {}))
        operator_data: dict[str, Any] = data.get("operator", {})

        # Only pass recognised fields to OperatorConfig
        operator_kwargs: dict[str, Any] = {}
        if "agent_timeout" in operator_data:
            operator_kwargs["agent_timeout"] = operator_data["agent_timeout"]

        # Only pass recognised fields to AgentConfig
        agent_field_names = {
            "backend",
            "model",  # accepted from TOML, handled separately
            "location",  # accepted from TOML, handled separately
            "thinking_budget",  # accepted from TOML, handled separately
            "max_retries",
            "retry_base_delay",
            "rate_limit_backoff",
        }
        unknown_keys = set(agent_data.keys()) - agent_field_names
        if unknown_keys:
            raise ValueError(f"Unrecognised key(s) in [agent]: {', '.join(sorted(unknown_keys))}")

        # Pop flat model keys
        _raw_model: str | None = agent_data.pop("model", None)
        _raw_location: str | None = agent_data.pop("location", None)
        _raw_thinking_budget: int | None = agent_data.pop("thinking_budget", None)

        # Build model_config from flat keys
        _raw_backend: str = str(agent_data.get("backend", "codex")).lower()
        _UNRESOLVABLE = {"subagent", "hybrid"}
        _agent_mc: ModelConfig | None = None
        if _raw_model:
            if _raw_backend not in _UNRESOLVABLE:
                try:
                    _agent_mc = from_provider_and_model(
                        _raw_backend, _raw_model, location=_raw_location, thinking_budget=_raw_thinking_budget
                    )
                except ValueError:
                    pass
            else:
                _agent_mc = ModelConfig.from_string(
                    _raw_model, location=_raw_location, thinking_budget=_raw_thinking_budget
                )

        agent_kwargs: dict[str, Any] = {**agent_data, "model_config": _agent_mc}

        return cls(
            agent=AgentConfig(**agent_kwargs),
            operator=OperatorConfig(**operator_kwargs),
        )


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge update dict into base dict."""
    for k, v in update.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)  # pyright: ignore[reportUnknownArgumentType]
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
    merged_data: dict[str, Any] = {}

    if config_path:
        files_to_load = [Path(config_path)]
    else:
        project_root = Path(__file__).resolve().parent.parent

        files_to_load: list[Path] = []

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
