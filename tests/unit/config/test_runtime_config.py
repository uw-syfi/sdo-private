from unittest.mock import patch

import pytest

from app_operator.adk.operator import AdkOperator
from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.operator import LangGraphOperator


def test_runtime_impl_langgraph_accepts_provider_and_model():
    config = Config.from_dict(
        {
            "runtime": {"impl": "langgraph"},
            "agent": {"backend": "openai", "model": "gpt-4o-mini"},
        }
    )
    assert config.runtime.impl == "langgraph"
    assert config.agent.backend == "openai"
    assert config.agent.model == "gpt-4o-mini"


def test_runtime_impl_adk_accepts_model():
    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"backend": "gemini", "model": "gemini-2.0-flash"},
        }
    )
    assert config.runtime.impl == "adk"


def test_runtime_impl_adk_still_validates_provider():
    """Test that provider validation still happens for adk runtime."""
    with pytest.raises(ValueError, match="Invalid backend"):
        Config.from_dict(
            {
                "runtime": {"impl": "adk"},
                "agent": {"backend": "invalid_provider", "model": "gemini-2.0-flash"},
            }
        )


def test_langgraph_operator_requires_model(tmp_path):
    """LangGraphOperator.__init__ raises if agent.model is not set."""
    fs = InMemoryFilesystem()
    fs.mkdir(tmp_path)
    config = Config.from_dict(
        {
            "runtime": {"impl": "langgraph"},
            "agent": {"backend": "openai"},
        }
    )
    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph"),
        patch("app_operator.langgraph.operator.TrajectoryRecorder"),
        pytest.raises(ValueError, match="agent.model must be set for langgraph runtime"),
    ):
        LangGraphOperator(repo_path=str(tmp_path), filesystem=fs, config=config)


def test_langgraph_operator_requires_provider(tmp_path):
    """LangGraphOperator.__init__ raises if agent.backend evaluates to falsy."""
    fs = InMemoryFilesystem()
    fs.mkdir(tmp_path)
    config = Config.from_dict(
        {
            "runtime": {"impl": "langgraph"},
            "agent": {"backend": "openai", "model": "gpt-4o-mini"},
        }
    )
    # Manually blank out provider to simulate missing value after construction
    config.agent.backend = ""
    with (
        patch("app_operator.langgraph.operator.build_llm"),
        patch("app_operator.langgraph.operator.build_graph"),
        patch("app_operator.langgraph.operator.TrajectoryRecorder"),
        pytest.raises(ValueError, match="agent.backend must be set for langgraph runtime"),
    ):
        LangGraphOperator(repo_path=str(tmp_path), filesystem=fs, config=config)


def test_adk_operator_requires_model(tmp_path):
    """AdkOperator.__init__ raises if agent.model is not set."""
    fs = InMemoryFilesystem()
    fs.directories.add(str(tmp_path))
    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"backend": "gemini", "model": "gemini-2.0-flash"},
        }
    )
    # Manually blank out model to simulate missing value after construction
    config.agent.model = None
    with (
        patch("app_operator.adk.operator.build_adk_model"),
        patch("app_operator.adk.operator.build_tools"),
        patch("app_operator.adk.operator.AdkAgentRunner"),
        pytest.raises(ValueError, match="agent.model must be set for adk runtime"),
    ):
        AdkOperator(repo_path=str(tmp_path), filesystem=fs, config=config)
