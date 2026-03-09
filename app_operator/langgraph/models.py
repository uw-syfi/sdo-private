import litellm

from app_operator.logger import logger

DEFAULT_LIMIT = 128000


def get_model_context_limit(model_name: str) -> int:
    """Get the context window limit for a given model name.

    Queries litellm's model database dynamically. Falls back to DEFAULT_LIMIT
    if the model is not recognised.
    """
    try:
        info = litellm.get_model_info(model_name)
        limit = info.get("max_input_tokens")
        if limit:
            return int(limit)
    except Exception:
        pass

    logger.warning(f"Context window limit not found for model '{model_name}', using default {DEFAULT_LIMIT}")
    return DEFAULT_LIMIT
