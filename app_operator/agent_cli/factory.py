from typing import Optional

from app_operator.config import load_config, Config
from .base import CodingAgent
from .codex import CodexCodingAgent
from .gemini import GeminiCodingAgent
from .opencode import OpencodeCodingAgent
from .claude import ClaudeCodeCodingAgent


def create_agent_from_config(
    target_dir: str,
    model_override: Optional[str] = None,
    config_path: Optional[str] = None,
    config: Optional[Config] = None,
) -> CodingAgent:
    """Create a coding agent based on configuration file.

    Looks for sds.toml or config.toml in the target directory, or uses the
    explicitly provided config path.
    Default to CodexCodingAgent if no config found or provider is not specified.

    Args:
        target_dir: Directory to look for configuration files (if config_path not set).
        model_override: Optional model name to override config.
        config_path: Optional explicit path to configuration file.
        config: Optional Config object. If provided, skips loading from file.

    Returns:
        CodingAgent: Configured coding agent.
    """
    if config is None:
        config = load_config(target_dir, config_path)

    provider = config.agent.provider
    model = model_override or config.agent.model

    print(f"Initializing coding agent provider: {provider}")
    if model:
        print(f"Using coding agent model: {model}")

    provider_lower = provider.lower()
    if provider_lower == "gemini":
        return GeminiCodingAgent(model=model)
    elif provider_lower == "opencode":
        return OpencodeCodingAgent(model=model)
    elif provider_lower in ("claude", "claude-code"):
        return ClaudeCodeCodingAgent(model=model)
    else:
        return CodexCodingAgent(model=model)
