from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from loguru import logger

from lego_agent.config import Config
from libs.model_config import normalize_provider


def build_llm(config: Config) -> BaseChatModel:
    provider = normalize_provider(config.agent.backend)
    model = config.agent.model
    location = config.agent.location
    if model is None:
        raise ValueError("Model must be specified for langgraph agent")

    if provider == "openai":
        return ChatOpenAI(model=model)
    if provider == "anthropic":
        kwargs: dict[str, Any] = {"model": model}
        if config.agent.thinking_budget:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": config.agent.thinking_budget,
            }
        return ChatAnthropic(**kwargs)  # type: ignore[reportArgumentType]
    if provider == "gemini":
        try:
            gkwargs: dict[str, Any] = {"model": model}
            if config.agent.thinking_budget:
                gkwargs["thinking_budget"] = config.agent.thinking_budget
                gkwargs["include_thoughts"] = True
            return ChatGoogleGenerativeAI(**gkwargs)
        except Exception as e:
            # If API key is missing, try falling back to Vertex AI
            if "API key required" in str(e):
                logger.info("Gemini API key not found, falling back to Vertex AI")
                vkwargs: dict[str, Any] = {"model": model, "vertexai": True}
                if location:
                    vkwargs["location"] = location
                if config.agent.thinking_budget:
                    vkwargs["thinking_budget"] = config.agent.thinking_budget
                    vkwargs["include_thoughts"] = True
                return ChatGoogleGenerativeAI(**vkwargs)
            raise
    if provider == "vertex":
        vx_kwargs: dict[str, Any] = {"model": model, "vertexai": True}
        if location:
            vx_kwargs["location"] = location
        if config.agent.thinking_budget:
            vx_kwargs["thinking_budget"] = config.agent.thinking_budget
            vx_kwargs["include_thoughts"] = True
        return ChatGoogleGenerativeAI(**vx_kwargs)

    raise ValueError(f"Unsupported langgraph provider: {config.agent.backend}")
