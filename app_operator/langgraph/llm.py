from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

from app_operator.config import Config


def _normalize_provider(provider: str) -> str:
    provider_lower = provider.lower()
    if provider_lower in ("codex", "opencode", "openai"):
        return "openai"
    if provider_lower in ("claude", "claude-code", "anthropic"):
        return "anthropic"
    if provider_lower == "gemini":
        return "gemini"
    return provider_lower


def build_llm(config: Config) -> BaseChatModel:
    provider = _normalize_provider(config.agent.provider)
    model = config.agent.model

    if provider == "openai":
        return ChatOpenAI(model=model)
    if provider == "anthropic":
        return ChatAnthropic(model=model)
    if provider == "gemini":
        return ChatGoogleGenerativeAI(model=model)

    raise ValueError(f"Unsupported langgraph provider: {config.agent.provider}")
