from app_operator.config import load_config, Config
from app_operator.logger import logger
from libs.agent_cli.base import CodingAgent, AGENT_REGISTRY


def create_agent_from_config(
    target_dir: str,
    model_override: str | None = None,
    config_path: str | None = None,
    config: Config | None = None,
) -> CodingAgent:
    """Create a coding agent based on configuration file.

    Looks for sds.toml or config.toml in the target directory, or uses the
    explicitly provided config path.
    Raises ValueError if the provider is not recognized.

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

    if provider_lower in AGENT_REGISTRY:
        kwargs = {"model": model}
        if provider_lower in ("rlm", "subagent", "hybrid"):
            kwargs["location"] = config.agent.location
        if provider_lower in ("rlm", "subagent", "hybrid"):
            kwargs["dspy_config"] = config.dspy
        return AGENT_REGISTRY[provider_lower](**kwargs)

    available = sorted(AGENT_REGISTRY.keys())
    raise ValueError(
        f"Unknown agent provider '{provider}'. "
        f"Available providers: {available}"
    )
