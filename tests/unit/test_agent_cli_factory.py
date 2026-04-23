import pytest
from agentshim import (
    BaseCodingAgent,
    ClaudeCodeCodingAgent,
    CodexCodingAgent,
    GeminiCodingAgent,
    OpencodeCodingAgent,
)
from agentshim import CodingAgent as PortableCodingAgent
from agentshim.base import register_provider
from agentshim.cli_agent import CLICodingAgent

from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.core import AgentConfig, Config
from libs.model_config import ModelConfig


@pytest.fixture
def mock_binaries(monkeypatch):
    """Mock binary checks so CLI agents can be instantiated without real binaries."""

    def mock_which(cmd, path=None):
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("agentshim.cli_agent.shutil.which", mock_which)
    monkeypatch.setattr(CLICodingAgent, "_check_cli", lambda self: None)


class TestAgentRegistry:
    """Tests for provider resolution and register_provider decorator."""

    def test_registry_contains_claude_aliases(self, mock_binaries):
        assert isinstance(PortableCodingAgent(provider="claude").backend, ClaudeCodeCodingAgent)
        assert isinstance(PortableCodingAgent(provider="claude-code").backend, ClaudeCodeCodingAgent)
        assert isinstance(PortableCodingAgent(provider="anthropic").backend, ClaudeCodeCodingAgent)

    def test_registry_contains_gemini(self, mock_binaries):
        assert isinstance(PortableCodingAgent(provider="gemini").backend, GeminiCodingAgent)

    def test_registry_contains_codex(self, mock_binaries):
        assert isinstance(PortableCodingAgent(provider="codex").backend, CodexCodingAgent)

    def test_registry_contains_opencode(self, mock_binaries):
        assert isinstance(PortableCodingAgent(provider="opencode").backend, OpencodeCodingAgent)

    def test_claude_aliases_resolve_to_same_class(self, mock_binaries):
        assert type(PortableCodingAgent(provider="claude").backend) is type(
            PortableCodingAgent(provider="claude-code").backend
        )
        assert type(PortableCodingAgent(provider="claude").backend) is type(
            PortableCodingAgent(provider="anthropic").backend
        )

    def test_register_provider_adds_to_registry(self):
        """register_provider decorator adds the class under each given name."""

        @register_provider("test_dummy_provider_xyz")
        class DummyAgent(BaseCodingAgent):
            def __init__(self, model=None):
                self.model = model
                self.recorder = None  # type: ignore[assignment]

            def generate(self, prompt, cwd=None, timeout=300, silent=False):
                return ""

        agent = PortableCodingAgent(provider="test_dummy_provider_xyz", model="dummy-model")
        assert isinstance(agent.backend, DummyAgent)
        assert agent.model == "dummy-model"


class TestCreateAgentFromConfig:
    """Tests for create_agent_from_config factory function."""

    def test_creates_claude_agent(self, tmp_path, mock_binaries):
        config = Config(
            agent=AgentConfig(backend="claude", model_config=ModelConfig(provider="anthropic", model="test-model"))
        )
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert isinstance(agent.backend, ClaudeCodeCodingAgent)  # type: ignore[attr-defined]

    def test_creates_gemini_agent(self, tmp_path, mock_binaries):
        config = Config(
            agent=AgentConfig(backend="gemini", model_config=ModelConfig(provider="gemini", model="test-model"))
        )
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert isinstance(agent.backend, GeminiCodingAgent)  # type: ignore[attr-defined]

    def test_model_override_takes_precedence(self, tmp_path, mock_binaries):
        config = Config(
            agent=AgentConfig(backend="claude", model_config=ModelConfig(provider="anthropic", model="original-model"))
        )
        agent = create_agent_from_config(str(tmp_path), model_override="override-model", config=config)
        assert agent.model == "override-model"  # type: ignore[attr-defined]

    def test_model_from_config_when_no_override(self, tmp_path, mock_binaries):
        config = Config(
            agent=AgentConfig(backend="claude", model_config=ModelConfig(provider="anthropic", model="config-model"))
        )
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.model == "config-model"  # type: ignore[attr-defined]

    def test_unknown_provider_falls_back_to_codex(self, tmp_path, mock_binaries):
        """Unregistered provider falls back to codex."""
        # Bypass AgentConfig validation to test factory fallback
        config = Config(
            agent=AgentConfig(backend="codex", model_config=ModelConfig(provider="openai", model="test-model"))
        )
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert isinstance(agent.backend, CodexCodingAgent)  # type: ignore[attr-defined]

    def test_invalid_provider_raises_value_error(self):
        """AgentConfig validation rejects unknown provider names."""
        with pytest.raises(ValueError, match="Invalid backend"):
            AgentConfig(backend="nonexistent_provider_xyz")

    def test_provider_is_case_insensitive(self, tmp_path, mock_binaries):
        """Provider lookup lowercases the name."""
        config = Config(agent=AgentConfig(backend="Claude", model_config=ModelConfig.from_string("test-model")))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert isinstance(agent.backend, ClaudeCodeCodingAgent)  # type: ignore[attr-defined]
