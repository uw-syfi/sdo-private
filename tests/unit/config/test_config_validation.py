"""Tests for configuration validation.

These tests verify that invalid configuration values are rejected with
clear error messages, preventing runtime errors from bad configurations.
"""

import pytest
from app_operator.config import AgentConfig, OperatorConfig, Config

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import given, strategies as st, settings
    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

    def given(*args, **kwargs):
        return pytest.mark.skip(reason="hypothesis not installed")

    class DummySettings:
        def __call__(self, *args, **kwargs):
            return pytest.mark.skip(reason="hypothesis not installed")

    class DummyStrategies:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    settings = DummySettings()
    st = DummyStrategies()


class TestOperatorConfigValidation:
    """Tests for OperatorConfig validation."""

    def test_interval_type_checked(self):
        """Interval must be an integer, not a string."""
        with pytest.raises(TypeError, match="interval must be int"):
            OperatorConfig(interval="30")

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
        assert config.deployment_max_iters == 20
        assert config.agent_fix_timeout == 1800
        assert config.deploy_timeout == 900
        assert config.agent_timeout == 900

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

    def test_provider_case_insensitive(self):
        """Provider names should be case-insensitive and normalized."""
        config = AgentConfig(provider="CODEX")
        assert config.provider == "codex"

        config = AgentConfig(provider="Gemini")
        assert config.provider == "gemini"

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

    def test_monitoring_max_iters_exactly_one(self):
        """Test monitoring_max_iters at minimum valid value (1)."""
        config = OperatorConfig(monitoring_max_iters=1)
        assert config.monitoring_max_iters == 1

    def test_deployment_max_iters_exactly_one(self):
        """Test deployment_max_iters at minimum valid value (1)."""
        config = OperatorConfig(deployment_max_iters=1)
        assert config.deployment_max_iters == 1

    def test_very_large_timeout_values(self):
        """Test very large but valid timeout values."""
        config = OperatorConfig(
            agent_fix_timeout=86400,  # 24 hours
            deploy_timeout=7200,       # 2 hours
            agent_timeout=3600         # 1 hour
        )
        assert config.agent_fix_timeout == 86400
        assert config.deploy_timeout == 7200
        assert config.agent_timeout == 3600

    def test_config_with_float_interval_rejected(self):
        """Test that float values for interval are rejected."""
        with pytest.raises(TypeError, match="interval must be int"):
            OperatorConfig(interval=30.5)

    def test_provider_with_whitespace(self):
        """Test provider with leading/trailing whitespace is rejected."""
        # Provider validation doesn't strip whitespace, so this should fail
        with pytest.raises(ValueError, match="Invalid provider"):
            AgentConfig(provider="  codex  ")

    def test_empty_model_string(self):
        """Test empty string for model."""
        config = AgentConfig(model="")
        assert config.model == ""

    def test_very_long_model_name(self):
        """Test very long model name."""
        long_model = "a" * 1000
        config = AgentConfig(model=long_model)
        assert config.model == long_model

    def test_special_characters_in_model_name(self):
        """Test special characters in model name."""
        config = AgentConfig(model="model-v1.5-beta_2024")
        assert config.model == "model-v1.5-beta_2024"

    def test_unicode_in_model_name(self):
        """Test Unicode characters in model name."""
        config = AgentConfig(model="模型-v1")
        assert config.model == "模型-v1"


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed - install with: uv add --dev hypothesis")
class TestOperatorConfigIntervalProperty:
    """Property-based tests for OperatorConfig interval boundary sweep."""

    @given(interval=st.integers(min_value=1, max_value=86400))
    @settings(max_examples=50, deadline=1000)
    def test_any_valid_interval_accepted(self, interval):
        """Any integer in [1, 86400] is accepted and stored correctly on OperatorConfig."""
        config = OperatorConfig(interval=interval)
        assert config.interval == interval

    @given(interval=st.integers().filter(lambda x: x < 1 or x > 86400))
    @settings(max_examples=50, deadline=1000)
    def test_any_invalid_interval_rejected(self, interval):
        """Any integer outside [1, 86400] raises ValueError when constructing OperatorConfig."""
        with pytest.raises(ValueError):
            OperatorConfig(interval=interval)


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed - install with: uv add --dev hypothesis")
class TestOperatorConfigTimeoutsProperty:
    """Property-based tests for OperatorConfig timeout and iteration fields."""

    @given(st.data())
    @settings(max_examples=50, deadline=1000)
    def test_any_positive_timeout_accepted(self, data):
        """Any positive integer for a timeout/max-iters field is accepted and stored correctly."""
        field = data.draw(st.sampled_from([
            "agent_fix_timeout",
            "deploy_timeout",
            "agent_timeout",
            "monitoring_max_iters",
            "deployment_max_iters",
        ]))
        value = data.draw(st.integers(min_value=1))
        config = OperatorConfig(**{field: value})
        assert getattr(config, field) == value

    @given(st.data())
    @settings(max_examples=50, deadline=1000)
    def test_any_non_positive_timeout_rejected(self, data):
        """Any non-positive integer for a timeout/max-iters field raises ValueError."""
        field = data.draw(st.sampled_from([
            "agent_fix_timeout",
            "deploy_timeout",
            "agent_timeout",
            "monitoring_max_iters",
            "deployment_max_iters",
        ]))
        value = data.draw(st.integers(max_value=0))
        with pytest.raises(ValueError):
            OperatorConfig(**{field: value})


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestConfigFromDictRoundTripProperty:
    """Property-based round-trip tests for Config.from_dict."""

    @given(
        provider=st.sampled_from(sorted(["codex", "gemini", "claude", "claude-code",
                                 "opencode", "anthropic", "vertex", "openai", "rlm", "subagent", "hybrid"])),
        interval=st.integers(min_value=1, max_value=86400),
    )
    @settings(max_examples=50, deadline=1000)
    def test_valid_provider_and_interval_roundtrip(self, provider, interval):
        """Config built from a dict preserves provider and interval exactly."""
        config = Config.from_dict({"agent": {"provider": provider}, "operator": {"interval": interval}})
        assert config.agent.provider == provider
        assert config.operator.interval == interval

    @given(
        section_name=st.text(min_size=1, max_size=30).filter(
            lambda s: s not in {"agent", "operator", "deployment", "runtime", "dspy", "fault_injection"}
        ),
    )
    @settings(max_examples=50, deadline=1000)
    def test_unknown_section_always_rejected(self, section_name):
        """Any unknown top-level section key always raises an error."""
        with pytest.raises(Exception):
            Config.from_dict({section_name: {}})

    @given(st.just({}))
    @settings(max_examples=1, deadline=1000)
    def test_default_config_from_empty_dict(self, data):
        """Config.from_dict({}) always produces defaults matching Config()."""
        assert Config.from_dict({}).agent.provider == Config().agent.provider
        assert Config.from_dict({}).operator.interval == Config().operator.interval
