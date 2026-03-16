"""Tests for model string builder."""

import pytest

from app_operator.config import AgentConfig, Config, RuntimeConfig
from app_operator.pydantic_ai._models import build_model_str


def _config(provider: str, model: str) -> Config:
    return Config(agent=AgentConfig(provider=provider, model=model))


def test_openai_provider():
    assert build_model_str(_config("openai", "gpt-4o")) == "openai:gpt-4o"


def test_codex_provider():
    assert build_model_str(_config("codex", "gpt-4o")) == "openai:gpt-4o"


def test_opencode_provider():
    assert build_model_str(_config("opencode", "gpt-4o")) == "openai:gpt-4o"


def test_anthropic_provider():
    assert build_model_str(_config("anthropic", "claude-3-opus")) == "anthropic:claude-3-opus"


def test_claude_provider():
    assert build_model_str(_config("claude", "claude-3-sonnet")) == "anthropic:claude-3-sonnet"


def test_claude_code_provider():
    assert build_model_str(_config("claude-code", "claude-3-haiku")) == "anthropic:claude-3-haiku"


def test_gemini_provider():
    assert build_model_str(_config("gemini", "gemini-2.0-flash")) == "google-gla:gemini-2.0-flash"


def test_vertex_provider():
    assert build_model_str(_config("vertex", "gemini-2.0-flash")) == "google-vertex:gemini-2.0-flash"


def test_model_already_has_prefix():
    assert build_model_str(_config("openai", "openai:gpt-4o")) == "openai:gpt-4o"


def test_heuristic_claude():
    assert build_model_str(_config("hybrid", "claude-3-opus")) == "anthropic:claude-3-opus"


def test_heuristic_gpt():
    assert build_model_str(_config("hybrid", "gpt-4")) == "openai:gpt-4"


def test_heuristic_gemini():
    assert build_model_str(_config("hybrid", "gemini-pro")) == "google-gla:gemini-pro"


def test_fallback_unknown():
    # "hybrid" has no canonical mapping so heuristics run; "some-model" has no
    # recognisable substring, so it falls back to openai.  The old bare-string
    # return value was a bug — pydantic-ai cannot dispatch unqualified strings.
    assert build_model_str(_config("hybrid", "some-model")) == "openai:some-model"


def test_no_model_raises():
    with pytest.raises(ValueError, match="agent.model must be set"):
        build_model_str(
            Config(
                agent=AgentConfig(provider="openai", model=None),
                runtime=RuntimeConfig(impl="pydantic_ai"),
            )
        )
