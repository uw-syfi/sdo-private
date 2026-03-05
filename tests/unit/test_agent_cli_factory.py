import pytest

from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.config import AgentConfig, Config
from libs.agent_cli.base import AGENT_REGISTRY, CodingAgent, register_provider
from libs.agent_cli.cli_agent import CLICodingAgent


@pytest.fixture
def mock_binaries(monkeypatch):
    """Mock binary checks so CLI agents can be instantiated without real binaries."""

    def mock_which(cmd, path=None):
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("libs.agent_cli.cli_agent.shutil.which", mock_which)
    monkeypatch.setattr(CLICodingAgent, "_check_cli", lambda self: None)


class TestAgentRegistry:
    """Tests for the AGENT_REGISTRY contents and register_provider decorator."""

    def test_registry_contains_claude_aliases(self):
        assert "claude" in AGENT_REGISTRY
        assert "claude-code" in AGENT_REGISTRY
        assert "anthropic" in AGENT_REGISTRY

    def test_registry_contains_gemini(self):
        assert "gemini" in AGENT_REGISTRY

    def test_registry_contains_codex(self):
        assert "codex" in AGENT_REGISTRY

    def test_registry_contains_opencode(self):
        assert "opencode" in AGENT_REGISTRY

    def test_claude_aliases_resolve_to_same_class(self):
        assert AGENT_REGISTRY["claude"] is AGENT_REGISTRY["claude-code"]
        assert AGENT_REGISTRY["claude"] is AGENT_REGISTRY["anthropic"]

    def test_register_provider_adds_to_registry(self):
        """register_provider decorator adds the class under each given name."""

        @register_provider("test_dummy_provider_xyz")
        class DummyAgent(CodingAgent):
            def __init__(self, model=None):
                self.model = model
                self.recorder = None

            def generate(self, prompt, cwd=None, timeout=300, silent=False):
                return ""

        assert "test_dummy_provider_xyz" in AGENT_REGISTRY
        assert AGENT_REGISTRY["test_dummy_provider_xyz"] is DummyAgent
        # Cleanup
        del AGENT_REGISTRY["test_dummy_provider_xyz"]


class TestCreateAgentFromConfig:
    """Tests for create_agent_from_config factory function."""

    def test_creates_claude_agent(self, tmp_path, mock_binaries):
        config = Config(agent=AgentConfig(provider="claude", model="test-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.__class__.__name__ == "ClaudeCodeCodingAgent"

    def test_creates_gemini_agent(self, tmp_path, mock_binaries):
        config = Config(agent=AgentConfig(provider="gemini", model="test-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.__class__.__name__ == "GeminiCodingAgent"

    def test_model_override_takes_precedence(self, tmp_path, mock_binaries):
        config = Config(agent=AgentConfig(provider="claude", model="original-model"))
        agent = create_agent_from_config(str(tmp_path), model_override="override-model", config=config)
        assert agent.model == "override-model"

    def test_model_from_config_when_no_override(self, tmp_path, mock_binaries):
        config = Config(agent=AgentConfig(provider="claude", model="config-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.model == "config-model"

    def test_unknown_provider_falls_back_to_codex(self, tmp_path, mock_binaries):
        """Unregistered provider falls back to codex."""
        # Bypass AgentConfig validation to test factory fallback
        config = Config(agent=AgentConfig(provider="codex", model="test-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.__class__.__name__ == "CodexCodingAgent"

    def test_invalid_provider_raises_value_error(self):
        """AgentConfig validation rejects unknown provider names."""
        with pytest.raises(ValueError, match="Invalid provider"):
            AgentConfig(provider="nonexistent_provider_xyz")

    def test_provider_is_case_insensitive(self, tmp_path, mock_binaries):
        """Provider lookup lowercases the name."""
        config = Config(agent=AgentConfig(provider="Claude", model="test-model"))
        agent = create_agent_from_config(str(tmp_path), config=config)
        assert agent.__class__.__name__ == "ClaudeCodeCodingAgent"
