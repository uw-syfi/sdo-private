import pytest

from app_operator.config import Config, RuntimeConfig


def test_runtime_valid_impls_are_cli_agent_and_pydantic_ai():
    assert {"cli_agent", "pydantic_ai"} == RuntimeConfig.VALID_IMPLS


def test_runtime_impl_invalid_raises_value_error():
    with pytest.raises(ValueError, match="Invalid impl"):
        Config.from_dict({"runtime": {"impl": "langgraph"}})


def test_runtime_impl_adk_raises_value_error():
    with pytest.raises(ValueError, match="Invalid impl"):
        Config.from_dict({"runtime": {"impl": "adk"}})


def test_runtime_impl_cli_agent_accepted():
    config = Config.from_dict({"runtime": {"impl": "cli_agent"}})
    assert config.runtime.impl == "cli_agent"


def test_runtime_impl_pydantic_ai_accepted():
    config = Config.from_dict({"runtime": {"impl": "pydantic_ai"}})
    assert config.runtime.impl == "pydantic_ai"
