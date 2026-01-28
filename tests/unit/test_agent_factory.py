from app_operator.cli_agent.backend.factory import create_agent_from_config
from app_operator.cli_agent.backend.base import CodingAgent, register_provider
from app_operator.config import Config, AgentConfig


@register_provider("mock_provider")
class MockAgent(CodingAgent):
    def __init__(self, model=None):
        self.model = model

    def generate(self, prompt, cwd=None, timeout=300, silent=False):
        return "mock response"


def test_create_agent_registered_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(
        AgentConfig,
        "VALID_PROVIDERS",
        AgentConfig.VALID_PROVIDERS | {"mock_provider"},
    )
    config = Config(agent=AgentConfig(provider="mock_provider", model="test-model"))
    agent = create_agent_from_config(str(tmp_path), config=config)

    assert isinstance(agent, MockAgent)
    assert agent.model == "test-model"


def test_create_agent_gemini(tmp_path):
    config = Config(agent=AgentConfig(provider="gemini"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "GeminiCodingAgent"


def test_create_agent_codex_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        AgentConfig,
        "VALID_PROVIDERS",
        AgentConfig.VALID_PROVIDERS | {"unknown_provider"},
    )
    config = Config(agent=AgentConfig(provider="unknown_provider"))
    # Should fallback to codex
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "CodexCodingAgent"


def test_create_agent_claude_alias(tmp_path):
    config = Config(agent=AgentConfig(provider="anthropic"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "ClaudeCodeCodingAgent"


def test_create_agent_opencode(tmp_path):
    config = Config(agent=AgentConfig(provider="opencode"))
    agent = create_agent_from_config(str(tmp_path), config=config)
    assert agent.__class__.__name__ == "OpencodeCodingAgent"
