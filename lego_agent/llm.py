from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from loguru import logger

from lego_agent.config import Config


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


def build_llm(config: Config) -> BaseChatModel:
    provider = _normalize_provider(config.agent.provider)
    model = config.agent.model
    location = config.agent.location
    if model is None:
        raise ValueError("Model must be specified for langgraph agent")

    if provider == "openai":
        return ChatOpenAI(model=model)
    if provider == "anthropic":
        kwargs = {"model": model}
        if config.agent.thinking_budget:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": config.agent.thinking_budget,
            }
        return ChatAnthropic(**kwargs)
    if provider == "gemini":
        try:
            kwargs = {"model": model}
            if config.agent.thinking_budget:
                kwargs["thinking_budget"] = config.agent.thinking_budget
                kwargs["include_thoughts"] = True
            return ChatGoogleGenerativeAI(**kwargs)
        except Exception as e:
            # If API key is missing, try falling back to Vertex AI
            if "API key required" in str(e):
                logger.info("Gemini API key not found, falling back to Vertex AI")
                kwargs = {"model": model, "vertexai": True}
                if location:
                    kwargs["location"] = location
                if config.agent.thinking_budget:
                    kwargs["thinking_budget"] = config.agent.thinking_budget
                    kwargs["include_thoughts"] = True
                return ChatGoogleGenerativeAI(**kwargs)
            raise
    if provider == "vertex":
        kwargs = {"model": model, "vertexai": True}
        if location:
            kwargs["location"] = location
        if config.agent.thinking_budget:
            kwargs["thinking_budget"] = config.agent.thinking_budget
            kwargs["include_thoughts"] = True
        return ChatGoogleGenerativeAI(**kwargs)

    raise ValueError(f"Unsupported langgraph provider: {config.agent.provider}")
