"""Token cost calculation utilities.

Calculates token costs based on model pricing.
"""

import logging

logger = logging.getLogger(__name__)

# Model pricing per 1M tokens (as of 2026-01)
MODEL_PRICING = {
    # Claude models (Anthropic)
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet-20240229": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},

    # GPT models (OpenAI)
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},

    # Gemini models (Google)
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate cost in USD for token usage.

    Args:
        model: Model name (e.g., 'claude-sonnet-4-5')
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Cost in USD, or 0.0 if model pricing not found
    """
    # Normalize: strip provider prefix
    normalized = model.split("/")[-1] if "/" in model else model
    if normalized not in MODEL_PRICING:
        logger.warning("No pricing data for model '%s', returning zero cost", model)
        return 0.0

    pricing = MODEL_PRICING[normalized]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return input_cost + output_cost


def get_model_pricing(model: str) -> dict[str, float] | None:
    """Get pricing information for a model.

    Args:
        model: Model name

    Returns:
        Dictionary with 'input' and 'output' prices per 1M tokens,
        or None if model not found
    """
    pricing = MODEL_PRICING.get(model)
    return dict(pricing) if pricing else None
