import importlib

from app_operator.config import Config, load_config
from app_operator.logger import logger

from .base import AGENT_REGISTRY, CodingAgent

for _module_name in ("claude", "codex", "gemini", "opencode"):
    importlib.import_module(f"{__package__}.{_module_name}")


def create_agent_from_config(
    target_dir: str,
    model_override: str | None = None,
    config_path: str | None = None,
    config: Config | None = None,
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

    provider = config.agent.backend
    model = model_override or config.agent.model

    logger.info(f"Initializing coding agent provider: {provider}")
    if model:
        logger.info(f"Using coding agent model: {model}")

    provider_lower = provider.lower()

    if provider_lower in AGENT_REGISTRY:
        return AGENT_REGISTRY[provider_lower](model=model)

    # Default fallback
    return AGENT_REGISTRY["codex"](model=model)
