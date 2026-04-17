from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app_operator.core import logger
from libs.model_config import normalize_provider


def _build_vertex_kwargs(
    model: str,
    location: str | None = None,
    thinking_budget: int | None = None,
) -> dict[str, Any]:
    """Build kwargs for ChatGoogleGenerativeAI with Vertex AI."""
    kwargs: dict[str, Any] = {"model": model, "vertexai": True}
    if location:
        kwargs["location"] = location
    if thinking_budget:
        kwargs["thinking_budget"] = thinking_budget
        kwargs["include_thoughts"] = True
    return kwargs


def create_chat_model(
    provider: str,
    model: str,
    location: str | None = None,
    thinking_budget: int | None = None,
) -> BaseChatModel:
    """Create a LangChain chat model from provider and model strings.

    This is the low-level factory that can be used without a full Config object.
    """
    normalized = normalize_provider(provider)

    if normalized == "openai":
        return ChatOpenAI(model=model)
    if normalized == "anthropic":
        kwargs: dict[str, Any] = {"model": model}
        if thinking_budget:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        return ChatAnthropic(**kwargs)
    if normalized == "gemini":
        try:
            kwargs = {"model": model}
            if thinking_budget:
                kwargs["thinking_budget"] = thinking_budget
                kwargs["include_thoughts"] = True
            return ChatGoogleGenerativeAI(**kwargs)
        except (ValueError, RuntimeError) as e:
            if "API key required" in str(e):
                logger.info("Gemini API key not found, falling back to Vertex AI")
                return ChatGoogleGenerativeAI(**_build_vertex_kwargs(model, location, thinking_budget))
            raise
    if normalized == "vertex":
        return ChatGoogleGenerativeAI(**_build_vertex_kwargs(model, location, thinking_budget))

    raise ValueError(f"Unsupported provider: {provider}")
