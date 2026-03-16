"""Model string builder for pydantic_ai operator.

Maps SDS provider/model config to Pydantic AI model identifiers.
"""

from pydantic_ai.settings import ModelSettings

from app_operator.config import Config
from libs.model_config import from_string
from libs.pydantic_agent import thinking_settings


def build_model_str(config: Config) -> str:
    """Build a pydantic-ai model string from SDS config.

    Returns a string like ``"openai:gpt-4o"`` or ``"google-gla:gemini-2.0-flash"``.
    """
    model = config.agent.model
    if not model:
        raise ValueError("agent.model must be set for pydantic_ai runtime")
    return from_string(model, provider_hint=config.agent.provider).to_pydantic_ai_str()


# Local fallbacks for models missing context_window in genai_prices.
# Track: https://github.com/pydantic/genai-prices/blob/main/prices/providers/google.yml
_CONTEXT_WINDOW_FALLBACKS: dict[str, int] = {
    "gemini-2.5-pro": 1_000_000,
}


def get_context_window(model_str: str) -> int | None:
    """Look up the context window size for a pydantic-ai model string.

    Args:
        model_str: A pydantic-ai model string like ``"anthropic:claude-3-5-sonnet-latest"``.

    Returns:
        Context window size in tokens, or ``None`` if unknown.
    """
    from genai_prices.data_snapshot import get_snapshot

    provider_id, model_name = model_str.split(":", 1) if ":" in model_str else (None, model_str)

    try:
        _, model_info = get_snapshot().find_provider_model(model_name, None, provider_id, None)
    except LookupError:
        return _CONTEXT_WINDOW_FALLBACKS.get(model_name)

    return model_info.context_window or _CONTEXT_WINDOW_FALLBACKS.get(model_name)


def build_model_settings(config: Config) -> ModelSettings | None:
    """Build pydantic-ai ModelSettings from SDS config, or None if no special settings."""
    budget = config.agent.thinking_budget
    if not budget:
        return None

    model_str = build_model_str(config)
    settings = thinking_settings(model_str, budget)
    return settings or None  # type: ignore[return-value]
