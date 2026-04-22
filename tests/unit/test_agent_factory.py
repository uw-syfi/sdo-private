from unittest.mock import patch

import pytest
from agentshim.base import CodingAgent, register_provider
from agentshim.cli_agent import CLICodingAgent

from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.core import AgentConfig, Config
from libs.model_config import ModelConfig


@pytest.fixture
def mock_binaries(monkeypatch):
    """Mock binary checks for agent creation."""

    # Only mock specific binaries that we expect to be present for these tests
    allowed_binaries = {"gemini", "codex", "claude", "opencode"}

    def mock_which(cmd, path=None):
        if cmd in allowed_binaries:
            return f"/usr/bin/{cmd}"
        return None

    # We need to patch shutil in the cli_agent module where it's used
    monkeypatch.setattr("agentshim.cli_agent.shutil.which", mock_which)

    # Also mock _check_cli to avoid running subprocess
    monkeypatch.setattr(CLICodingAgent, "_check_cli", lambda self: None)


@register_provider("mock_provider")
class MockAgent(CodingAgent):
    def __init__(self, model=None):
        self.model = model
        self.recorder = None  # type: ignore[assignment]

    def generate(self, prompt, cwd=None, timeout=300, silent=False):
        return "mock response"


def test_create_agent_registered_provider(tmp_path):
    # Patch VALID_BACKENDS to allow mock_provider
    with patch(
        "app_operator.core.config.AgentConfig.VALID_BACKENDS",
        {
            "mock_provider",
            "codex",
            "gemini",
            "claude",
            "claude-code",
            "opencode",
            "anthropic",
            "vertex",
            "openai",
        },
    ):
        config = Config(agent=AgentConfig(backend="mock_provider", model_config=ModelConfig.from_string("test-model")))
        agent = create_agent_from_config(str(tmp_path), config=config)

        assert isinstance(agent, MockAgent)
        assert agent.model == "test-model"  # type: ignore[attr-defined]


def test_create_agent_gemini(tmp_path, mock_binaries):
    config = Config(
        agent=AgentConfig(backend="gemini", model_config=ModelConfig(provider="gemini", model="test-model"))
    )
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "GeminiCodingAgent"


def test_create_agent_codex_default(tmp_path):
    # With strict validation, unknown provider should raise ValueError
    with pytest.raises(ValueError, match="Invalid backend"):
        Config(agent=AgentConfig(backend="unknown_provider"))


def test_create_agent_claude_alias(tmp_path, mock_binaries):
    config = Config(
        agent=AgentConfig(backend="anthropic", model_config=ModelConfig(provider="anthropic", model="test-model"))
    )
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "ClaudeCodeCodingAgent"


def test_create_agent_opencode(tmp_path, mock_binaries):
    config = Config(
        agent=AgentConfig(backend="opencode", model_config=ModelConfig(provider="openai", model="test-model"))
    )
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "OpencodeCodingAgent"


def test_create_agent_unregistered_provider_raises_valueerror(tmp_path):
    """Factory raises ValueError with available providers for unregistered provider."""
    # Bypass AgentConfig validation by patching VALID_BACKENDS
    with patch(
        "app_operator.core.config.AgentConfig.VALID_BACKENDS",
        {"not_registered", "codex", "gemini", "claude", "claude-code", "opencode", "anthropic", "vertex", "openai"},
    ):
        config = Config(agent=AgentConfig(backend="not_registered", model_config=ModelConfig.from_string("m")))
        with pytest.raises(ValueError, match="Unknown agent backend"):
            create_agent_from_config(str(tmp_path), config=config)
