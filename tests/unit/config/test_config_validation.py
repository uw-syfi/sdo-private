"""Tests for configuration validation.

These tests verify that invalid configuration values are rejected with
clear error messages, preventing runtime errors from bad configurations.
"""

import pytest
from app_operator.config import AgentConfig, OperatorConfig, Config


class TestOperatorConfigValidation:
    """Tests for OperatorConfig validation."""

    def test_interval_must_be_positive(self):
        """Interval must be a positive integer."""
        with pytest.raises(ValueError, match="interval must be positive"):
            OperatorConfig(interval=-1)

    def test_interval_cannot_be_zero(self):
        """Interval cannot be zero."""
        with pytest.raises(ValueError, match="interval must be positive"):
            OperatorConfig(interval=0)

    def test_interval_type_checked(self):
        """Interval must be an integer, not a string."""
        with pytest.raises(TypeError, match="interval must be int"):
            OperatorConfig(interval="30")

    def test_interval_max_bound(self):
        """Interval cannot exceed 24 hours (86400 seconds)."""
        with pytest.raises(ValueError, match="interval too large"):
            OperatorConfig(interval=999999)

    def test_interval_max_bound_allowed(self):
        """Interval of exactly 24 hours should be allowed."""
        config = OperatorConfig(interval=86400)
        assert config.interval == 86400

    def test_monitoring_max_iters_must_be_positive(self):
        """Monitoring max iterations must be positive."""
        with pytest.raises(ValueError, match="monitoring_max_iters must be positive"):
            OperatorConfig(monitoring_max_iters=0)

    def test_monitoring_max_iters_negative(self):
        """Monitoring max iterations cannot be negative."""
        with pytest.raises(ValueError, match="monitoring_max_iters must be positive"):
            OperatorConfig(monitoring_max_iters=-5)

    def test_monitoring_max_iters_type_checked(self):
        """Monitoring max iterations must be an integer."""
        with pytest.raises(TypeError, match="monitoring_max_iters must be int"):
            OperatorConfig(monitoring_max_iters="5")

    def test_deployment_max_iters_must_be_positive(self):
        """Deployment max iterations must be positive."""
        with pytest.raises(ValueError, match="deployment_max_iters must be positive"):
            OperatorConfig(deployment_max_iters=0)

    def test_deployment_max_iters_negative(self):
        """Deployment max iterations cannot be negative."""
        with pytest.raises(ValueError, match="deployment_max_iters must be positive"):
            OperatorConfig(deployment_max_iters=-3)

    def test_deployment_max_iters_type_checked(self):
        """Deployment max iterations must be an integer."""
        with pytest.raises(TypeError, match="deployment_max_iters must be int"):
            OperatorConfig(deployment_max_iters=3.5)

    def test_valid_config_accepted(self):
        """Valid configuration values should be accepted."""
        config = OperatorConfig(
            interval=60, monitoring_max_iters=10, deployment_max_iters=3
        )
        assert config.interval == 60
        assert config.monitoring_max_iters == 10
        assert config.deployment_max_iters == 3

    def test_defaults_are_valid(self):
        """Default configuration should be valid."""
        config = OperatorConfig()
        assert config.interval == 30
        assert config.monitoring_max_iters == 5
        assert config.deployment_max_iters == 5
        assert config.agent_fix_timeout == 1800
        assert config.deploy_timeout == 900
        assert config.agent_timeout == 300

    def test_interval_boundary_values(self):
        """Test interval at boundary values."""
        # Minimum valid value
        config = OperatorConfig(interval=1)
        assert config.interval == 1

        # Just below maximum
        config = OperatorConfig(interval=86399)
        assert config.interval == 86399

        # Exactly at maximum
        config = OperatorConfig(interval=86400)
        assert config.interval == 86400

    def test_agent_fix_timeout_must_be_positive(self):
        """Agent fix timeout must be positive."""
        with pytest.raises(ValueError, match="agent_fix_timeout must be positive"):
            OperatorConfig(agent_fix_timeout=0)

    def test_agent_fix_timeout_type_checked(self):
        """Agent fix timeout must be an integer."""
        with pytest.raises(TypeError, match="agent_fix_timeout must be int"):
            OperatorConfig(agent_fix_timeout="1800")

    def test_deploy_timeout_must_be_positive(self):
        """Deploy timeout must be positive."""
        with pytest.raises(ValueError, match="deploy_timeout must be positive"):
            OperatorConfig(deploy_timeout=-1)

    def test_deploy_timeout_type_checked(self):
        """Deploy timeout must be an integer."""
        with pytest.raises(TypeError, match="deploy_timeout must be int"):
            OperatorConfig(deploy_timeout=900.5)

    def test_agent_timeout_must_be_positive(self):
        """Agent timeout must be positive."""
        with pytest.raises(ValueError, match="agent_timeout must be positive"):
            OperatorConfig(agent_timeout=0)

    def test_agent_timeout_type_checked(self):
        """Agent timeout must be an integer."""
        with pytest.raises(TypeError, match="agent_timeout must be int"):
            OperatorConfig(agent_timeout="300")

    def test_custom_timeout_values_accepted(self):
        """Custom timeout values should be accepted."""
        config = OperatorConfig(
            agent_fix_timeout=3600, deploy_timeout=1800, agent_timeout=600
        )
        assert config.agent_fix_timeout == 3600
        assert config.deploy_timeout == 1800
        assert config.agent_timeout == 600


class TestAgentConfigValidation:
    """Tests for AgentConfig validation."""

    def test_provider_validated(self):
        """Invalid provider should be rejected."""
        with pytest.raises(ValueError, match="Invalid provider"):
            AgentConfig(provider="invalid-provider")

    def test_provider_case_insensitive(self):
        """Provider names should be case-insensitive."""
        config = AgentConfig(provider="CODEX")
        assert config.provider == "CODEX"

        config = AgentConfig(provider="Gemini")
        assert config.provider == "Gemini"

    def test_valid_providers_accepted(self):
        """All valid providers should be accepted."""
        valid_providers = ["codex", "gemini", "claude", "claude-code", "opencode"]
        for provider in valid_providers:
            config = AgentConfig(provider=provider)
            assert config.provider == provider

    def test_provider_type_checked(self):
        """Provider must be a string."""
        with pytest.raises(TypeError, match="provider must be str"):
            AgentConfig(provider=123)

    def test_model_type_checked(self):
        """Model must be a string or None."""
        with pytest.raises(TypeError, match="model must be str or None"):
            AgentConfig(model=123)

    def test_model_can_be_none(self):
        """Model can be None (optional)."""
        config = AgentConfig(provider="codex", model=None)
        assert config.model is None

    def test_model_can_be_string(self):
        """Model can be a string."""
        config = AgentConfig(provider="gemini", model="gemini-1.5-pro")
        assert config.model == "gemini-1.5-pro"

    def test_location_type_checked(self):
        """Location must be a string or None."""
        with pytest.raises(TypeError, match="location must be str or None"):
            AgentConfig(location=123)

    def test_location_can_be_none(self):
        """Location can be None (optional)."""
        config = AgentConfig(provider="codex", location=None)
        assert config.location is None

    def test_location_can_be_string(self):
        """Location can be a string."""
        config = AgentConfig(provider="vertex", location="us-west1")
        assert config.location == "us-west1"

    def test_default_config_valid(self):
        """Default agent config should be valid."""
        config = AgentConfig()
        assert config.provider == "codex"
        assert config.model is None

    def test_provider_error_message_includes_valid_options(self):
        """Error message should list valid provider options."""
        with pytest.raises(ValueError) as exc_info:
            AgentConfig(provider="bad-provider")

        error_msg = str(exc_info.value)
        assert "codex" in error_msg
        assert "gemini" in error_msg
        assert "claude" in error_msg


class TestConfigIntegration:
    """Tests for full Config object validation."""

    def test_full_config_with_valid_values(self):
        """Full configuration with valid values should work."""
        config = Config(
            agent=AgentConfig(provider="claude", model="claude-3"),
            operator=OperatorConfig(
                interval=60, monitoring_max_iters=10, deployment_max_iters=5
            ),
        )
        assert config.agent.provider == "claude"
        assert config.agent.model == "claude-3"
        assert config.operator.interval == 60
        assert config.operator.monitoring_max_iters == 10
        assert config.operator.deployment_max_iters == 5

    def test_default_full_config_valid(self):
        """Default full configuration should be valid."""
        config = Config()
        assert config.agent.provider == "codex"
        assert config.operator.interval == 30

    def test_config_from_dict_validates(self):
        """Config.from_dict should trigger validation."""
        with pytest.raises(ValueError, match="interval must be positive"):
            Config.from_dict({"operator": {"interval": -1}})

    def test_config_from_dict_with_invalid_provider(self):
        """Config.from_dict should validate provider."""
        with pytest.raises(ValueError, match="Invalid provider"):
            Config.from_dict({"agent": {"provider": "unknown"}})

    def test_config_from_dict_with_custom_timeouts(self):
        """Config.from_dict should accept custom timeout values."""
        config = Config.from_dict(
            {
                "operator": {
                    "agent_fix_timeout": 3600,
                    "deploy_timeout": 1800,
                    "agent_timeout": 600,
                }
            }
        )
        assert config.operator.agent_fix_timeout == 3600
        assert config.operator.deploy_timeout == 1800
        assert config.operator.agent_timeout == 600

    def test_config_from_dict_validates_timeout_types(self):
        """Config.from_dict should validate timeout types."""
        with pytest.raises(TypeError, match="agent_fix_timeout must be int"):
            Config.from_dict({"operator": {"agent_fix_timeout": "not-an-int"}})

    def test_config_from_dict_validates_timeout_values(self):
        """Config.from_dict should validate timeout values are positive."""
        with pytest.raises(ValueError, match="deploy_timeout must be positive"):
            Config.from_dict({"operator": {"deploy_timeout": -100}})
