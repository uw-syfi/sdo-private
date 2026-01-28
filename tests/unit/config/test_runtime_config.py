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
