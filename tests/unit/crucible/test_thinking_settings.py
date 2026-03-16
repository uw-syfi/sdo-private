"""Unit tests for _thinking_settings_for in sregym_agents.crucible.sre_agent."""

from __future__ import annotations

import pytest

from sregym_agents.crucible.sre_agent import THINKING_BUDGET, _thinking_settings_for


class TestThinkingSettingsFor:
    @pytest.mark.parametrize("model", [
        "claude-3.5-sonnet",
        "anthropic/claude-3-opus",
        "us.anthropic.claude-3-haiku",
    ])
    def test_claude_models_return_anthropic_thinking(self, model: str):
        settings = _thinking_settings_for(model)
        assert settings == {
            "anthropic_thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET},
        }

    @pytest.mark.parametrize("model", [
        "gemini-2.5-flash",
        "google-vertex:gemini-2.5-pro",
    ])
    def test_gemini_models_return_thinking_config_with_include_thoughts(self, model: str):
        settings = _thinking_settings_for(model)
        assert settings == {
            "gemini_thinking_config": {
                "thinking_budget": THINKING_BUDGET,
                "include_thoughts": True,
            },
        }

    @pytest.mark.parametrize("model", [
        "gpt-4o",
        "o3-mini",
        "some-unknown-model",
    ])
    def test_unsupported_models_return_empty(self, model: str):
        settings = _thinking_settings_for(model)
        assert settings == {}
