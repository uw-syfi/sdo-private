from langchain_core.language_models.chat_models import BaseChatModel

from app_operator.config import Config
from app_operator.llm import create_chat_model
from app_operator.logger import logger

__all__ = ["create_chat_model"]


def build_llm(config: Config) -> BaseChatModel:
    """Build LLM from a full Config. Delegates to create_chat_model."""
    model = config.agent.model
    if model is None:
        raise ValueError("Model must be specified for langgraph agent")

    logger.info(f"Using model: {config.agent.backend}/{model}")
    return create_chat_model(
        provider=config.agent.backend,
        model=model,
        location=config.agent.location,
        thinking_budget=config.agent.thinking_budget,
    )
