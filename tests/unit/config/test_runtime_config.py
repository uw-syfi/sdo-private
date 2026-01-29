import pytest

from app_operator.config import Config


def test_runtime_impl_langgraph_requires_model_and_provider():
    with pytest.raises(ValueError):
        Config.from_dict({"runtime": {"impl": "langgraph"}})


def test_runtime_impl_langgraph_accepts_provider_and_model():
    config = Config.from_dict(
        {
            "runtime": {"impl": "langgraph"},
            "agent": {"provider": "openai", "model": "gpt-4o-mini"},
        }
    )
    assert config.runtime.impl == "langgraph"
    assert config.agent.provider == "openai"
    assert config.agent.model == "gpt-4o-mini"


def test_runtime_impl_adk_requires_model():
    with pytest.raises(ValueError, match="agent.model must be set for adk runtime"):
        Config.from_dict({"runtime": {"impl": "adk"}})


def test_runtime_impl_adk_accepts_model():
    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"provider": "gemini", "model": "gemini-2.0-flash"},
        }
    )
    assert config.runtime.impl == "adk"


def test_runtime_impl_adk_still_validates_provider():
    """Test that provider validation still happens for adk runtime."""
    with pytest.raises(ValueError, match="Invalid provider"):
        Config.from_dict(
            {
                "runtime": {"impl": "adk"},
                "agent": {"provider": "invalid_provider", "model": "gemini-2.0-flash"},
            }
        )
