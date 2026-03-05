"""Tests for FeaturesConfig."""

import pytest

from app_operator.config import Config, FeaturesConfig, UnrecognizedFieldError

_AGENT = {"agent": {"provider": "codex", "model": "test-model"}}


class TestFeaturesConfig:
    """Test FeaturesConfig dataclass."""

    def test_default_values(self):
        config = FeaturesConfig()
        assert config.git_integration is False

    def test_git_integration_enabled(self):
        config = FeaturesConfig(git_integration=True)
        assert config.git_integration is True

    def test_rejects_non_bool(self):
        with pytest.raises(TypeError, match="git_integration must be bool"):
            FeaturesConfig(git_integration="yes")

        with pytest.raises(TypeError, match="git_integration must be bool"):
            FeaturesConfig(git_integration=1)


class TestFeaturesConfigParsing:
    """Test parsing of [features] section from TOML."""

    def test_defaults_to_false(self):
        config = Config.from_dict({**_AGENT})
        assert config.features.git_integration is False

    def test_enabled_via_config(self):
        config = Config.from_dict({**_AGENT, "features": {"git_integration": True}})
        assert config.features.git_integration is True

    def test_rejects_non_bool_via_config(self):
        with pytest.raises(TypeError):
            Config.from_dict({**_AGENT, "features": {"git_integration": "yes"}})

    def test_unrecognized_field_error(self):
        with pytest.raises(UnrecognizedFieldError, match=r"Unrecognized field\(s\) in \[features\] section"):
            Config.from_dict({**_AGENT, "features": {"git_integration": True, "unknown": True}})

    def test_empty_features_section(self):
        config = Config.from_dict({**_AGENT, "features": {}})
        assert config.features.git_integration is False
