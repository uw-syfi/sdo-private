from typing import Optional

from app_operator.config import load_config, Config
from app_operator.logger import logger
from .base import CodingAgent, AGENT_REGISTRY

# Import modules to ensure agents are registered
from . import codex, gemini, opencode, claude, rlm_agent, filtered_agent  # noqa: F401


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

    logger.info(f"Initializing coding agent provider: {provider}")
    if model:
        logger.info(f"Using coding agent model: {model}")

    provider_lower = provider.lower()

    if provider_lower == "rlm":
        return AGENT_REGISTRY["rlm"](model=model, location=config.agent.location)

    if provider_lower in AGENT_REGISTRY:
        kwargs = {"model": model}
        if provider_lower in ("rlm", "filtered"):
            kwargs["location"] = config.agent.location
        return AGENT_REGISTRY[provider_lower](**kwargs)

    # Default fallback
    return AGENT_REGISTRY["codex"](model=model)
