"""Comprehensive TDD tests for libs.model_config.ModelConfig."""

from __future__ import annotations

import pytest

from libs.model_config import ModelConfig, from_provider_and_model, from_string


class TestFromProviderAndModel:
    """Tests for ModelConfig.from_provider_and_model factory."""

    @pytest.mark.parametrize(
        ("alias", "expected_canonical"),
        [
            ("openai", "openai"),
            ("codex", "openai"),
            ("opencode", "openai"),
            ("anthropic", "anthropic"),
            ("claude", "anthropic"),
            ("claude-code", "anthropic"),
            ("gemini", "gemini"),
            ("rlm", "gemini"),
            ("vertex", "vertex"),
        ],
    )
    def test_all_aliases_map_to_canonical(self, alias: str, expected_canonical: str):
        mc = from_provider_and_model(alias, "some-model")
        assert mc.provider == expected_canonical

    def test_location_and_thinking_budget_preserved(self):
        mc = from_provider_and_model("vertex", "gemini-2.5-pro", location="us-central1", thinking_budget=4096)
        assert mc.location == "us-central1"
        assert mc.thinking_budget == 4096

    def test_unknown_alias_raises(self):
        with pytest.raises(ValueError, match="Unknown provider alias"):
            from_provider_and_model("badprovider", "some-model")

    def test_subagent_raises(self):
        with pytest.raises(ValueError, match="no canonical family mapping"):
            from_provider_and_model("subagent", "some-model")

    def test_hybrid_raises(self):
        with pytest.raises(ValueError, match="no canonical family mapping"):
            from_provider_and_model("hybrid", "some-model")


class TestFromString:
    """Tests for ModelConfig.from_string factory — full coverage required."""

    # --- colon-prefix (pydantic-ai style) ---

    def test_openai_colon_prefix(self):
        mc = from_string("openai:gpt-4o")
        assert mc.provider == "openai"
        assert mc.model == "gpt-4o"

    def test_anthropic_colon_prefix(self):
        mc = from_string("anthropic:claude-3-7-sonnet")
        assert mc.provider == "anthropic"
        assert mc.model == "claude-3-7-sonnet"

    def test_google_gla_colon_prefix(self):
        mc = from_string("google-gla:gemini-2.0-flash")
        assert mc.provider == "gemini"
        assert mc.model == "gemini-2.0-flash"

    def test_google_vertex_colon_prefix(self):
        mc = from_string("google-vertex:gemini-2.5-pro")
        assert mc.provider == "vertex"
        assert mc.model == "gemini-2.5-pro"

    # --- slash-prefix (litellm style) ---

    def test_anthropic_slash_prefix(self):
        mc = from_string("anthropic/claude-3-opus")
        assert mc.provider == "anthropic"
        assert mc.model == "claude-3-opus"

    def test_gemini_slash_prefix(self):
        mc = from_string("gemini/gemini-2.0-flash")
        assert mc.provider == "gemini"
        assert mc.model == "gemini-2.0-flash"

    def test_vertex_ai_slash_prefix(self):
        mc = from_string("vertex_ai/gemini-2.5-pro")
        assert mc.provider == "vertex"
        assert mc.model == "gemini-2.5-pro"

    def test_openai_slash_prefix(self):
        mc = from_string("openai/gpt-4o")
        assert mc.provider == "openai"
        assert mc.model == "gpt-4o"

    # --- bare model strings (heuristics) ---

    def test_bare_claude_heuristic(self):
        mc = from_string("claude-3.5-sonnet")
        assert mc.provider == "anthropic"
        assert mc.model == "claude-3.5-sonnet"

    def test_bare_gemini_heuristic(self):
        mc = from_string("gemini-2.5-flash")
        assert mc.provider == "gemini"
        assert mc.model == "gemini-2.5-flash"

    def test_bare_gpt_heuristic(self):
        mc = from_string("gpt-4o")
        assert mc.provider == "openai"
        assert mc.model == "gpt-4o"

    def test_bare_o3_heuristic(self):
        mc = from_string("o3-mini")
        assert mc.provider == "openai"
        assert mc.model == "o3-mini"

    def test_bare_unknown_fallback_openai(self):
        mc = from_string("some-model")
        assert mc.provider == "openai"
        assert mc.model == "some-model"

    # --- provider_hint ---

    def test_provider_hint_vertex_overrides_gemini_heuristic(self):
        mc = from_string("gemini-2.5-pro", provider_hint="vertex")
        assert mc.provider == "vertex"
        assert mc.model == "gemini-2.5-pro"

    def test_provider_hint_claude_code_maps_to_anthropic(self):
        mc = from_string("claude-3.5-sonnet", provider_hint="claude-code")
        assert mc.provider == "anthropic"

    def test_provider_hint_hybrid_ignored_uses_heuristic(self):
        # "hybrid" is unresolvable, so heuristic takes over
        mc = from_string("gemini-2.5-pro", provider_hint="hybrid")
        assert mc.provider == "gemini"

    # --- passthrough of optional fields ---

    def test_location_and_budget_passed_through(self):
        mc = from_string("google-vertex:gemini-2.5-pro", location="eu-west1", thinking_budget=8192)
        assert mc.location == "eu-west1"
        assert mc.thinking_budget == 8192


class TestToPydanticAiStr:
    def test_openai(self):
        mc = ModelConfig(provider="openai", model="gpt-4o")
        assert mc.to_pydantic_ai_str() == "openai:gpt-4o"

    def test_anthropic(self):
        mc = ModelConfig(provider="anthropic", model="claude-3-opus")
        assert mc.to_pydantic_ai_str() == "anthropic:claude-3-opus"

    def test_gemini(self):
        mc = ModelConfig(provider="gemini", model="gemini-2.0-flash")
        assert mc.to_pydantic_ai_str() == "google-gla:gemini-2.0-flash"

    def test_vertex(self):
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")
        assert mc.to_pydantic_ai_str() == "google-vertex:gemini-2.5-pro"


class TestToLitellmStr:
    def test_openai(self):
        mc = ModelConfig(provider="openai", model="gpt-4o")
        assert mc.to_litellm_str() == "openai/gpt-4o"

    def test_anthropic(self):
        mc = ModelConfig(provider="anthropic", model="claude-3-opus")
        assert mc.to_litellm_str() == "anthropic/claude-3-opus"

    def test_gemini(self):
        mc = ModelConfig(provider="gemini", model="gemini-2.0-flash")
        assert mc.to_litellm_str() == "gemini/gemini-2.0-flash"

    def test_vertex(self):
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")
        assert mc.to_litellm_str() == "vertex_ai/gemini-2.5-pro"


class TestToPydanticAiSettings:
    BUDGET = 4096

    def test_anthropic_thinking(self):
        mc = ModelConfig(provider="anthropic", model="claude-3-sonnet", thinking_budget=self.BUDGET)
        assert mc.to_pydantic_ai_settings() == {"anthropic_thinking": {"type": "enabled", "budget_tokens": self.BUDGET}}

    def test_gemini_thinking(self):
        mc = ModelConfig(provider="gemini", model="gemini-2.5-pro", thinking_budget=self.BUDGET)
        assert mc.to_pydantic_ai_settings() == {
            "gemini_thinking_config": {"thinking_budget": self.BUDGET, "include_thoughts": True}
        }

    def test_vertex_thinking(self):
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro", thinking_budget=self.BUDGET)
        assert mc.to_pydantic_ai_settings() == {
            "google_thinking_config": {"thinking_budget": self.BUDGET, "include_thoughts": True}
        }

    def test_openai_no_op(self):
        mc = ModelConfig(provider="openai", model="gpt-4o", thinking_budget=self.BUDGET)
        assert mc.to_pydantic_ai_settings() == {}

    def test_no_budget_returns_empty(self):
        mc = ModelConfig(provider="anthropic", model="claude-3-sonnet")
        assert mc.to_pydantic_ai_settings() == {}

    def test_budget_tokens_override(self):
        mc = ModelConfig(provider="anthropic", model="claude-3-sonnet")
        result = mc.to_pydantic_ai_settings(budget_tokens=8192)
        assert result == {"anthropic_thinking": {"type": "enabled", "budget_tokens": 8192}}

    def test_budget_tokens_override_beats_stored(self):
        mc = ModelConfig(provider="gemini", model="gemini-2.5-pro", thinking_budget=1000)
        result = mc.to_pydantic_ai_settings(budget_tokens=2000)
        assert result["gemini_thinking_config"]["thinking_budget"] == 2000


class TestRoundTrip:
    """from_string(mc.to_pydantic_ai_str()) round-trips for all 4 providers."""

    @pytest.mark.parametrize(
        "mc",
        [
            ModelConfig(provider="openai", model="gpt-4o"),
            ModelConfig(provider="anthropic", model="claude-3-opus"),
            ModelConfig(provider="gemini", model="gemini-2.0-flash"),
            ModelConfig(provider="vertex", model="gemini-2.5-pro"),
        ],
    )
    def test_pydantic_ai_round_trip(self, mc: ModelConfig):
        restored = from_string(mc.to_pydantic_ai_str())
        assert restored.provider == mc.provider
        assert restored.model == mc.model

    @pytest.mark.parametrize(
        "mc",
        [
            ModelConfig(provider="openai", model="gpt-4o"),
            ModelConfig(provider="anthropic", model="claude-3-opus"),
            ModelConfig(provider="gemini", model="gemini-2.0-flash"),
            ModelConfig(provider="vertex", model="gemini-2.5-pro"),
        ],
    )
    def test_litellm_round_trip(self, mc: ModelConfig):
        restored = from_string(mc.to_litellm_str())
        assert restored.provider == mc.provider
        assert restored.model == mc.model


class TestFieldValidation:
    """__post_init__ validation on ModelConfig."""

    def test_empty_model_raises(self):
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            ModelConfig(provider="openai", model="")

    def test_whitespace_model_raises(self):
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            ModelConfig(provider="openai", model="   ")

    def test_non_canonical_provider_raises(self):
        with pytest.raises(ValueError, match="provider must be one of"):
            ModelConfig(provider="claude", model="claude-3-opus")

    def test_thinking_budget_zero_raises(self):
        with pytest.raises(ValueError, match="thinking_budget must be a positive int"):
            ModelConfig(provider="anthropic", model="claude-3-sonnet", thinking_budget=0)

    def test_thinking_budget_negative_raises(self):
        with pytest.raises(ValueError, match="thinking_budget must be a positive int"):
            ModelConfig(provider="anthropic", model="claude-3-sonnet", thinking_budget=-1)

    def test_valid_construction_no_error(self):
        mc = ModelConfig(
            provider="vertex",
            model="gemini-2.5-pro",
            location="us-central1",
            thinking_budget=8192,
        )
        assert mc.provider == "vertex"
        assert mc.model == "gemini-2.5-pro"
        assert mc.location == "us-central1"
        assert mc.thinking_budget == 8192


class TestValidateEnv:
    """validate_env() checks required environment variables."""

    def test_openai_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mc = ModelConfig(provider="openai", model="gpt-4o")
        mc.validate_env()  # should not raise

    def test_openai_missing_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        mc = ModelConfig(provider="openai", model="gpt-4o")
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
            mc.validate_env()

    def test_anthropic_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mc = ModelConfig(provider="anthropic", model="claude-3-sonnet")
        mc.validate_env()

    def test_anthropic_missing_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mc = ModelConfig(provider="anthropic", model="claude-3-sonnet")
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            mc.validate_env()

    def test_gemini_with_gemini_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        mc = ModelConfig(provider="gemini", model="gemini-2.0-flash")
        mc.validate_env()

    def test_gemini_with_google_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "key")
        mc = ModelConfig(provider="gemini", model="gemini-2.0-flash")
        mc.validate_env()

    def test_gemini_missing_both_keys(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        mc = ModelConfig(provider="gemini", model="gemini-2.0-flash")
        with pytest.raises(EnvironmentError, match="GEMINI_API_KEY or GOOGLE_API_KEY"):
            mc.validate_env()

    def test_vertex_all_present_with_field_location(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/creds.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro", location="us-central1")
        mc.validate_env()

    def test_vertex_location_from_google_cloud_location_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/creds.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east1")
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")  # no field location
        mc.validate_env()

    def test_vertex_location_from_vertex_location_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/creds.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        monkeypatch.setenv("VERTEX_LOCATION", "europe-west4")
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")
        mc.validate_env()

    def test_vertex_missing_credentials(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")
        with pytest.raises(EnvironmentError, match="GOOGLE_APPLICATION_CREDENTIALS"):
            mc.validate_env()

    def test_vertex_missing_project(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/creds.json")
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")
        with pytest.raises(EnvironmentError, match="GOOGLE_CLOUD_PROJECT"):
            mc.validate_env()

    def test_vertex_missing_location_all_sources(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/creds.json")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)
        mc = ModelConfig(provider="vertex", model="gemini-2.5-pro")  # no field location
        with pytest.raises(EnvironmentError, match="location"):
            mc.validate_env()
