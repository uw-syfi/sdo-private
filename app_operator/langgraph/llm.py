from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

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


def build_llm(config: Config) -> BaseChatModel:
    provider = _normalize_provider(config.agent.provider)
    model = config.agent.model
    location = config.agent.location
    if model is None:
        raise ValueError("Model must be specified for langgraph agent")

    if provider == "openai":
        return ChatOpenAI(model=model)
    if provider == "anthropic":
        return ChatAnthropic(model=model)
    if provider == "gemini":
        try:
            return ChatGoogleGenerativeAI(model=model)
        except Exception as e:
            # If API key is missing, try falling back to Vertex AI
            if "API key required" in str(e):
                logger.info("Gemini API key not found, falling back to Vertex AI")
                kwargs = {"model": model, "vertexai": True}
                if location:
                    kwargs["location"] = location
                return ChatGoogleGenerativeAI(**kwargs)
            raise
    if provider == "vertex":
        kwargs = {"model": model, "vertexai": True}
        if location:
            kwargs["location"] = location
        return ChatGoogleGenerativeAI(**kwargs)

    raise ValueError(f"Unsupported langgraph provider: {config.agent.provider}")
