"""Shared thinking budget configuration for pydantic-ai model settings."""

from __future__ import annotations

from libs.model_config import from_string


def thinking_settings(model_str: str, budget_tokens: int) -> dict:
    """Return pydantic-ai model_settings enabling thinking for the given model.

    Handles model-family-specific key names. Returns empty dict if the model
    family does not support thinking config (e.g. OpenAI).

    Args:
        model_str: pydantic-ai model string (e.g. "anthropic:claude-3-7-sonnet"
                   or bare "gemini-2.5-pro" or "google-vertex:gemini-2.5-pro")
        budget_tokens: thinking token budget
    """
    return from_string(model_str, thinking_budget=budget_tokens).to_pydantic_ai_settings()
