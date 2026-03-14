"""Model string builder for pydantic_ai operator.

Maps SDS provider/model config to Pydantic AI model identifiers.
"""

from app_operator.config import Config

# Mapping from SDS provider names to pydantic-ai model prefixes.
_PROVIDER_TO_PAI_PREFIX: dict[str, str] = {
    "openai": "openai",
    "codex": "openai",
    "opencode": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude-code": "anthropic",
    "gemini": "google-gla",
    "vertex": "google-vertex",
}


def build_model_str(config: Config) -> str:
    """Build a pydantic-ai model string from SDS config.

    Returns a string like ``"openai:gpt-4o"`` or ``"google-gla:gemini-2.0-flash"``.
    """
    provider = config.agent.provider
    model = config.agent.model
    if not model:
        raise ValueError("agent.model must be set for pydantic_ai runtime")

    # If model already contains a provider prefix, return as-is.
    if ":" in model:
        return model

    prefix = _PROVIDER_TO_PAI_PREFIX.get(provider)
    if prefix is not None:
        return f"{prefix}:{model}"

    # Heuristic fallback: infer from model name.
    lower = model.lower()
    if "claude" in lower:
        return f"anthropic:{model}"
    if "gpt" in lower or "o1" in lower:
        return f"openai:{model}"
    if "gemini" in lower:
        return f"google-gla:{model}"

    return model
