from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app_operator.config import Config
from app_operator.logger import logger


def _normalize_provider(provider: str) -> str:
    provider_lower = provider.lower()
    if provider_lower in ("codex", "opencode", "openai"):
        return "openai"
    if provider_lower in ("claude", "claude-code", "anthropic"):
        return "anthropic"
    if provider_lower == "gemini":
        return "gemini"
    if provider_lower in ("vertex", "vertex-ai"):
        return "vertex"
    return provider_lower


def _build_vertex_kwargs(
    model: str,
    location: str | None = None,
    thinking_budget: int | None = None,
) -> dict:
    """Build kwargs for ChatGoogleGenerativeAI with Vertex AI."""
    kwargs: dict = {"model": model, "vertexai": True}
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
    normalized = _normalize_provider(provider)

    if normalized == "openai":
        return ChatOpenAI(model=model)
    if normalized == "anthropic":
        kwargs: dict = {"model": model}
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
        except Exception as e:
            if "API key required" in str(e):
                logger.info("Gemini API key not found, falling back to Vertex AI")
                return ChatGoogleGenerativeAI(**_build_vertex_kwargs(model, location, thinking_budget))
            raise
    if normalized == "vertex":
        return ChatGoogleGenerativeAI(**_build_vertex_kwargs(model, location, thinking_budget))

    raise ValueError(f"Unsupported provider: {provider}")


def build_llm(config: Config) -> BaseChatModel:
    """Build LLM from a full Config. Delegates to create_chat_model."""
    model = config.agent.model
    if model is None:
        raise ValueError("Model must be specified for langgraph agent")

    return create_chat_model(
        provider=config.agent.provider,
        model=model,
        location=config.agent.location,
        thinking_budget=config.agent.thinking_budget,
    )
