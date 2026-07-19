"""Unit tests for libs.pydantic_agent.thinking_settings."""

from __future__ import annotations

import pytest

from libs.pydantic_agent import thinking_settings

BUDGET = 4096


class TestThinkingSettings:
    @pytest.mark.parametrize(
        "model",
        [
            "claude-3.5-sonnet",
            "anthropic/claude-3-opus",
            "us.anthropic.claude-3-haiku",
            "anthropic:claude-3-7-sonnet",
        ],
    )
    def test_claude_models_return_anthropic_thinking(self, model: str):
        settings = thinking_settings(model, BUDGET)
        assert settings == {
            "anthropic_thinking": {"type": "enabled", "budget_tokens": BUDGET},
        }

    @pytest.mark.parametrize(
        "model",
        [
            "gemini-2.5-flash",
            "google-gla:gemini-2.0-flash",
        ],
    )
    def test_gemini_gla_models_return_google_thinking_config(self, model: str):
        settings = thinking_settings(model, BUDGET)
        assert settings == {
            "google_thinking_config": {
                "thinking_budget": BUDGET,
                "include_thoughts": True,
            },
        }

    @pytest.mark.parametrize(
        "model",
        [
            "google-vertex:gemini-2.5-pro",
            "google-vertex:gemini-2.0-flash",
        ],
    )
    def test_google_vertex_models_return_google_thinking_config(self, model: str):
        settings = thinking_settings(model, BUDGET)
        assert settings == {
            "google_thinking_config": {
                "thinking_budget": BUDGET,
                "include_thoughts": True,
            },
        }

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o",
            "o3-mini",
            "some-unknown-model",
        ],
    )
    def test_unsupported_models_return_empty(self, model: str):
        settings = thinking_settings(model, BUDGET)
        assert settings == {}

    def test_budget_tokens_propagated(self):
        settings = thinking_settings("claude-3-sonnet", 8192)
        assert settings["anthropic_thinking"]["budget_tokens"] == 8192  # type: ignore[typeddict-item]

        settings = thinking_settings("gemini-2.5-pro", 8192)
        assert settings["google_thinking_config"]["thinking_budget"] == 8192  # type: ignore[typeddict-item]
