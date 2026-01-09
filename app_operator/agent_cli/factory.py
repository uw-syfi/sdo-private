try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
import sys

from .base import CodingAgent
from .codex import CodexCodingAgent
from .gemini import GeminiCodingAgent


def create_agent_from_config(
        target_dir: str,
        model_override: Optional[str] = None,
        config_path: Optional[str] = None) -> CodingAgent:
    """Create a coding agent based on configuration file.

    Looks for sds.toml or config.toml in the target directory, or uses the
    explicitly provided config path.
    Default to CodexCodingAgent if no config found or provider is not specified.

    Args:
        target_dir: Directory to look for configuration files (if config_path not set).
        model_override: Optional model name to override config.
        config_path: Optional explicit path to configuration file.

    Returns:
        CodingAgent: Configured coding agent.
    """
    target_path = Path(target_dir)

    if config_path:
        config_files = [Path(config_path)]
    else:
        config_files = [target_path / "sds.toml", target_path / "config.toml"]

    provider = "codex"
    model = model_override

    for config_file in config_files:
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    config = tomllib.load(f)
                    agent_config = config.get("agent", {})
                    if "provider" in agent_config:
                        provider = agent_config["provider"]
                    if not model and "model" in agent_config:
                        model = agent_config["model"]
                print(f"Loaded configuration from {config_file}")
                break
            except Exception as e:
                print(
                    f"Warning: Failed to parse {config_file}: {e}",
                    file=sys.stderr)

    print(f"Initializing coding agent provider: {provider}")
    if model:
        print(f"Using coding agent model: {model}")

    if provider.lower() == "gemini":
        return GeminiCodingAgent(model=model)
    else:
        return CodexCodingAgent(model=model)
