from app_operator.config import Config


def build_adk_model(config: Config) -> str:
    """Return an ADK-compatible model handle for LlmAgent.

    For ADK, this is currently just the model name string for Gemini models.
    """
    if not config.agent.model:
        raise ValueError("agent.model must be set for ADK runtime")

    provider = config.agent.provider.lower()

    # Only Gemini/Vertex providers are supported by ADK runtime
    if provider not in ("gemini", "vertex"):
        raise ValueError(
            f"ADK runtime only supports 'gemini' or 'vertex' providers, "
            f"got '{provider}'"
        )

    # Return the model name directly
    return config.agent.model
