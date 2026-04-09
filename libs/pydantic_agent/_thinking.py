"""Shared thinking budget configuration for pydantic-ai model settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic_ai.models import Model

from libs.model_config import from_string

if TYPE_CHECKING:
    from pydantic_ai.settings import ModelSettings


def thinking_settings(model_str: str | Model, budget_tokens: int) -> ModelSettings:
    """Return pydantic-ai model_settings enabling thinking for the given model.

    Handles model-family-specific key names. Returns empty dict if the model
    family does not support thinking config (e.g. OpenAI).

    Args:
        model_str: pydantic-ai model string (e.g. "anthropic:claude-3-7-sonnet"
                   or bare "gemini-2.5-pro" or "google-vertex:gemini-2.5-pro"),
                   or a pydantic-ai Model instance (uses its model_name).
        budget_tokens: thinking token budget
    """
    name = model_str.model_name if isinstance(model_str, Model) else model_str
    return cast("ModelSettings", from_string(name, thinking_budget=budget_tokens).to_pydantic_ai_settings())
