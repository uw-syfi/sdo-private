import pytest
from unittest.mock import patch
from libs.agent_cli.factory import create_agent_from_config
from libs.agent_cli.base import CodingAgent, register_provider
from libs.agent_cli.cli_agent import CLICodingAgent
from app_operator.config import Config, AgentConfig


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
    monkeypatch.setattr(
        "libs.agent_cli.cli_agent.shutil.which", mock_which
    )

    # Also mock _check_cli to avoid running subprocess
    monkeypatch.setattr(CLICodingAgent, "_check_cli", lambda self: None)


@register_provider("mock_provider")
class MockAgent(CodingAgent):
    def __init__(self, model=None):
        self.model = model
        self.recorder = None

    def generate(self, prompt, cwd=None, timeout=300, silent=False):
        return "mock response"


def test_create_agent_registered_provider(tmp_path):
    # Patch VALID_PROVIDERS to allow mock_provider
    with patch(
        "app_operator.config.AgentConfig.VALID_PROVIDERS",
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
        config = Config(agent=AgentConfig(provider="mock_provider", model="test-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)

        assert isinstance(agent, MockAgent)
        assert agent.model == "test-model"


def test_create_agent_gemini(tmp_path, mock_binaries):
    config = Config(agent=AgentConfig(provider="gemini"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "GeminiCodingAgent"


def test_create_agent_codex_default(tmp_path):
    # With strict validation, unknown provider should raise ValueError
    with pytest.raises(ValueError, match="Invalid provider"):
        Config(agent=AgentConfig(provider="unknown_provider"))


def test_create_agent_claude_alias(tmp_path, mock_binaries):
    config = Config(agent=AgentConfig(provider="anthropic"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "ClaudeCodeCodingAgent"


def test_create_agent_opencode(tmp_path, mock_binaries):
    config = Config(agent=AgentConfig(provider="opencode"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "OpencodeCodingAgent"
