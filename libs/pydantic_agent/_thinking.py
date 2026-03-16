"""Shared thinking budget configuration for pydantic-ai model settings."""

from __future__ import annotations


def thinking_settings(model_str: str, budget_tokens: int) -> dict:
    """Return pydantic-ai model_settings enabling thinking for the given model.

    Handles model-family-specific key names. Returns empty dict if the model
    family does not support thinking config (e.g. OpenAI).

    Args:
        model_str: pydantic-ai model string (e.g. "anthropic:claude-3-7-sonnet"
                   or bare "gemini-2.5-pro" or "google-vertex:gemini-2.5-pro")
        budget_tokens: thinking token budget
    """
    lower = model_str.lower()
    if "anthropic" in lower or "claude" in lower:
        return {"anthropic_thinking": {"type": "enabled", "budget_tokens": budget_tokens}}
    if "google-vertex" in lower:
        # google-vertex uses pydantic_ai.models.google.GoogleModel → "google_thinking_config"
        return {"google_thinking_config": {"thinking_budget": budget_tokens, "include_thoughts": True}}
    if "gemini" in lower or "google" in lower:
        # google-gla and bare gemini strings use pydantic_ai.models.gemini.GeminiModel → "gemini_thinking_config"
        return {"gemini_thinking_config": {"thinking_budget": budget_tokens, "include_thoughts": True}}
    return {}
