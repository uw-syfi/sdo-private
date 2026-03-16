import pytest

from lego_agent.config import AgentConfig, Config


def test_invalid_backend_raises():
    with pytest.raises(ValueError, match="Invalid backend"):
        AgentConfig(backend="invalid")


def test_valid_backends_accepted():
    for backend in AgentConfig.VALID_BACKENDS:
        config = AgentConfig(backend=backend)
        assert config.backend == backend.lower()


def test_unknown_agent_key_raises():
    with pytest.raises(ValueError, match="Unrecognised key"):
        Config.from_dict({"agent": {"typo_key": "val"}})


def test_known_agent_keys_accepted():
    config = Config.from_dict({"agent": {"backend": "gemini", "model": "gemini-2.0-flash"}})
    assert config.agent.backend == "gemini"
    assert config.agent.model == "gemini-2.0-flash"
