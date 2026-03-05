import pytest

from lego_agent.config import AgentConfig, Config


def test_invalid_provider_raises():
    with pytest.raises(ValueError, match="Invalid provider"):
        AgentConfig(provider="invalid")


def test_valid_providers_accepted():
    for provider in AgentConfig.VALID_PROVIDERS:
        config = AgentConfig(provider=provider)
        assert config.provider == provider.lower()


def test_unknown_agent_key_raises():
    with pytest.raises(ValueError, match="Unrecognised key"):
        Config.from_dict({"agent": {"typo_key": "val"}})


def test_known_agent_keys_accepted():
    config = Config.from_dict({"agent": {"provider": "gemini", "model": "gemini-2.0-flash"}})
    assert config.agent.provider == "gemini"
    assert config.agent.model == "gemini-2.0-flash"
