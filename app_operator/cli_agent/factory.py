from agentshim import BaseCodingAgent, CodingAgent
from app_operator.core import Config, load_config, logger


def create_agent_from_config(
    target_dir: str,
    model_override: str | None = None,
    config_path: str | None = None,
    config: Config | None = None,
) -> BaseCodingAgent:
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
        BaseCodingAgent: Configured coding agent.
    """
    if config is None:
        config = load_config(target_dir, config_path)

    backend = config.agent.backend
    model = model_override or config.agent.model

    logger.info(f"Initializing coding agent backend: {backend}")
    if model:
        logger.info(f"Using coding agent model: {model}")

    backend_kwargs: dict[str, object] | None = None
    if backend in ("rlm-official", "subagent", "hybrid"):
        backend_kwargs = {
            "location": config.agent.location,
            "dspy_config": config.dspy,  # type: ignore[reportArgumentType]
        }
    if backend == "hybrid":
        if backend_kwargs is None:
            backend_kwargs = {}
        backend_kwargs["rlm_mode"] = config.rlm.mode
    try:
        return CodingAgent(provider=backend, model=model, backend_kwargs=backend_kwargs)
    except ValueError as exc:
        raise ValueError(f"Unknown agent backend '{backend}': {exc}") from exc
