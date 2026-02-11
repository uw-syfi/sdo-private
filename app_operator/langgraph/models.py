from typing import Dict

# Map model prefixes or full names to context window sizes
# Using conservative estimates or standard limits
MODEL_LIMITS: Dict[str, int] = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    # Anthropic
    "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-2.1": 200000,
    "claude-2": 100000,
    # Google
    "gemini-1.5-pro": 2000000,
    "gemini-1.5-flash": 1000000,
    "gemini-1.0-pro": 32000,
}

DEFAULT_LIMIT = 128000


def get_model_context_limit(model_name: str) -> int:
    """Get the context window limit for a given model name."""
    model_lower = model_name.lower()

    # Direct match
    if model_lower in MODEL_LIMITS:
        return MODEL_LIMITS[model_lower]

    # Prefix match (longest match wins)
    matches = []
    for key, limit in MODEL_LIMITS.items():
        if model_lower.startswith(key):
            matches.append((len(key), limit))

    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]

    return DEFAULT_LIMIT
