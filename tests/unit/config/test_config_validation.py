"""Tests for configuration validation.

These tests verify that invalid configuration values are rejected with
clear error messages, preventing runtime errors from bad configurations.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app_operator.core import AgentConfig, Config, OperatorConfig
from libs.model_config import ModelConfig


class TestOperatorConfigValidation:
    """Tests for OperatorConfig validation."""

    def test_interval_type_checked(self):
        """Interval must be an integer, not a string."""
        with pytest.raises(TypeError, match="interval must be int"):
            OperatorConfig(interval="30")  # type: ignore[arg-type]

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
            OperatorConfig(monitoring_max_iters="5")  # type: ignore[arg-type]

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
            OperatorConfig(deployment_max_iters=3.5)  # type: ignore[arg-type]

    def test_valid_config_accepted(self):
        """Valid configuration values should be accepted."""
        config = OperatorConfig(interval=60, monitoring_max_iters=10, deployment_max_iters=3)
        assert config.interval == 60
        assert config.monitoring_max_iters == 10
        assert config.deployment_max_iters == 3

    def test_defaults_are_valid(self):
        """Default configuration should be valid."""
        config = OperatorConfig()
        assert config.interval == 30
        assert config.monitoring_max_iters == 5
        assert config.deployment_max_iters == 20
        assert config.agent_fix_timeout == 2700
        assert config.deploy_timeout == 900
        assert config.agent_timeout == 900

    def test_agent_fix_timeout_must_be_positive(self):
        """Agent fix timeout must be positive."""
        with pytest.raises(ValueError, match="agent_fix_timeout must be positive"):
            OperatorConfig(agent_fix_timeout=0)

    def test_agent_fix_timeout_type_checked(self):
        """Agent fix timeout must be an integer."""
        with pytest.raises(TypeError, match="agent_fix_timeout must be int"):
            OperatorConfig(agent_fix_timeout="1800")  # type: ignore[arg-type]

    def test_deploy_timeout_must_be_positive(self):
        """Deploy timeout must be positive."""
        with pytest.raises(ValueError, match="deploy_timeout must be positive"):
            OperatorConfig(deploy_timeout=-1)

    def test_deploy_timeout_type_checked(self):
        """Deploy timeout must be an integer."""
        with pytest.raises(TypeError, match="deploy_timeout must be int"):
            OperatorConfig(deploy_timeout=900.5)  # type: ignore[arg-type]

    def test_agent_timeout_must_be_positive(self):
        """Agent timeout must be positive."""
        with pytest.raises(ValueError, match="agent_timeout must be positive"):
            OperatorConfig(agent_timeout=0)

    def test_agent_timeout_type_checked(self):
        """Agent timeout must be an integer."""
        with pytest.raises(TypeError, match="agent_timeout must be int"):
            OperatorConfig(agent_timeout="300")  # type: ignore[arg-type]

    def test_custom_timeout_values_accepted(self):
        """Custom timeout values should be accepted."""
        config = OperatorConfig(agent_fix_timeout=3600, deploy_timeout=1800, agent_timeout=600)
        assert config.agent_fix_timeout == 3600
        assert config.deploy_timeout == 1800
        assert config.agent_timeout == 600


class TestAgentConfigValidation:
    """Tests for AgentConfig validation."""

    def test_provider_case_insensitive(self):
        """Provider names should be case-insensitive and normalized."""
        config = AgentConfig(backend="CODEX")
        assert config.backend == "codex"

        config = AgentConfig(backend="Gemini")
        assert config.backend == "gemini"

    def test_provider_type_checked(self):
        """Provider must be a string."""
        with pytest.raises(TypeError, match="backend must be str"):
            AgentConfig(backend=123)  # type: ignore[arg-type]

    def test_model_type_checked(self):
        """model_config must be a ModelConfig or None."""
        with pytest.raises(TypeError, match="model_config must be a ModelConfig or None"):
            AgentConfig(model_config="not-a-model-config")  # type: ignore[arg-type]

    def test_model_can_be_none(self):
        """Model can be None (optional)."""
        config = AgentConfig(backend="codex")
        assert config.model is None

    def test_model_can_be_string(self):
        """Model can be a string."""
        config = AgentConfig(backend="gemini", model_config=ModelConfig(provider="gemini", model="gemini-1.5-pro"))
        assert config.model == "gemini-1.5-pro"

    def test_location_can_be_none(self):
        """Location can be None (optional)."""
        config = AgentConfig(backend="codex")
        assert config.location is None

    def test_location_can_be_string(self):
        """Location can be a string."""
        config = AgentConfig(
            backend="vertex", model_config=ModelConfig(provider="vertex", model="gemini-1.5-pro", location="us-west1")
        )
        assert config.location == "us-west1"

    def test_default_config_valid(self):
        """Default agent config should be valid."""
        config = AgentConfig()
        assert config.backend == "codex"
        assert config.model is None

    def test_provider_error_message_includes_valid_options(self):
        """Error message should list valid provider options."""
        with pytest.raises(ValueError, match="Invalid backend") as exc_info:
            AgentConfig(backend="bad-provider")

        error_msg = str(exc_info.value)
        assert "codex" in error_msg
        assert "gemini" in error_msg
        assert "claude" in error_msg

    @pytest.mark.parametrize("backend", ["rlm"])
    def test_registered_architecture_backends_accepted(self, backend):
        """Registered architecture backends should be user-selectable."""
        config = AgentConfig(backend=backend)
        assert config.backend == backend


class TestConfigIntegration:
    """Tests for full Config object validation."""

    def test_full_config_with_valid_values(self):
        """Full configuration with valid values should work."""
        config = Config(
            agent=AgentConfig(backend="claude", model_config=ModelConfig(provider="anthropic", model="claude-3")),
            operator=OperatorConfig(interval=60, monitoring_max_iters=10, deployment_max_iters=5),
        )
        assert config.agent.backend == "claude"
        assert config.agent.model == "claude-3"
        assert config.operator.interval == 60
        assert config.operator.monitoring_max_iters == 10
        assert config.operator.deployment_max_iters == 5

    def test_default_full_config_valid(self):
        """Default full configuration should be valid."""
        config = Config(agent=AgentConfig(model_config=ModelConfig(provider="openai", model="test-model")))
        assert config.agent.backend == "codex"
        assert config.operator.interval == 30

    def test_config_from_dict_validates(self):
        """Config.from_dict should trigger validation."""
        with pytest.raises(ValueError, match="interval must be positive"):
            Config.from_dict({"operator": {"interval": -1}})

    def test_config_from_dict_with_invalid_provider(self):
        """Config.from_dict should validate provider."""
        with pytest.raises(ValueError, match="Invalid backend"):
            Config.from_dict({"agent": {"backend": "unknown"}})

    def test_config_from_dict_with_custom_timeouts(self):
        """Config.from_dict should accept custom timeout values."""
        config = Config.from_dict(
            {
                "agent": {"model": "test-model"},
                "operator": {
                    "agent_fix_timeout": 3600,
                    "deploy_timeout": 1800,
                    "agent_timeout": 600,
                },
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
            deploy_timeout=7200,  # 2 hours
            agent_timeout=3600,  # 1 hour
        )
        assert config.agent_fix_timeout == 86400
        assert config.deploy_timeout == 7200
        assert config.agent_timeout == 3600

    def test_config_with_float_interval_rejected(self):
        """Test that float values for interval are rejected."""
        with pytest.raises(TypeError, match="interval must be int"):
            OperatorConfig(interval=30.5)  # type: ignore[arg-type]

    def test_provider_with_whitespace(self):
        """Test provider with leading/trailing whitespace is rejected."""
        with pytest.raises(ValueError, match="Invalid backend"):
            AgentConfig(backend="  codex  ")

    def test_empty_model_string(self):
        """Test AgentConfig with no model (model_config=None)."""
        config = AgentConfig()
        assert config.model is None

    def test_very_long_model_name(self):
        """Test very long model name."""
        long_model = "a" * 1000
        config = AgentConfig(model_config=ModelConfig(provider="openai", model=long_model))
        assert config.model == long_model

    def test_special_characters_in_model_name(self):
        """Test special characters in model name."""
        config = AgentConfig(model_config=ModelConfig(provider="openai", model="model-v1.5-beta_2024"))
        assert config.model == "model-v1.5-beta_2024"

    def test_unicode_in_model_name(self):
        """Test Unicode characters in model name."""
        config = AgentConfig(model_config=ModelConfig(provider="openai", model="模型-v1"))
        assert config.model == "模型-v1"

    def test_config_from_dict_with_gepa_section(self):
        """Config.from_dict should parse [gepa] section."""
        config = Config.from_dict({"agent": {"model": "test-model"}, "gepa": {"max_steps": 100, "num_candidates": 20}})
        assert config.gepa.max_steps == 100
        assert config.gepa.num_candidates == 20

    def test_config_gepa_defaults(self):
        """Default Config should include valid GEPAConfig defaults."""
        config = Config(agent=AgentConfig(model_config=ModelConfig(provider="openai", model="test-model")))
        assert config.gepa.max_steps == 50
        assert config.gepa.num_candidates == 10
        assert config.gepa.reflection_provider == "gemini"

    def test_config_from_dict_validates_gepa_fields(self):
        """Config.from_dict should validate gepa field values."""
        with pytest.raises(ValueError, match="max_steps must be positive"):
            Config.from_dict({"gepa": {"max_steps": 0}})

    def test_config_from_dict_rejects_unknown_gepa_field(self):
        """Config.from_dict should reject unknown fields in [gepa]."""
        from app_operator.core import UnrecognizedFieldError

        with pytest.raises(UnrecognizedFieldError, match="unknown_field"):
            Config.from_dict({"gepa": {"unknown_field": "value"}})

    def test_config_gepa_diversity_probability(self):
        """Config.from_dict should accept diversity_probability."""
        config = Config.from_dict({"agent": {"model": "test-model"}, "gepa": {"diversity_probability": 0.3}})
        assert config.gepa.diversity_probability == 0.3

    def test_config_gepa_diversity_probability_invalid(self):
        """Config.from_dict should reject invalid diversity_probability."""
        with pytest.raises(ValueError, match="diversity_probability must be in range"):
            Config.from_dict({"gepa": {"diversity_probability": 2.0}})

    def test_config_gepa_patience(self):
        """Config.from_dict should accept patience."""
        config = Config.from_dict({"agent": {"model": "test-model"}, "gepa": {"patience": 20}})
        assert config.gepa.patience == 20

    def test_config_gepa_patience_invalid(self):
        """Config.from_dict should reject non-positive patience."""
        with pytest.raises(ValueError, match="patience must be positive"):
            Config.from_dict({"gepa": {"patience": 0}})

    def test_config_gepa_checkpoint_interval(self):
        """Config.from_dict should accept checkpoint_interval."""
        config = Config.from_dict({"agent": {"model": "test-model"}, "gepa": {"checkpoint_interval": 10}})
        assert config.gepa.checkpoint_interval == 10

    def test_config_gepa_checkpoint_interval_invalid(self):
        """Config.from_dict should reject non-positive checkpoint_interval."""
        with pytest.raises(ValueError, match="checkpoint_interval must be positive"):
            Config.from_dict({"gepa": {"checkpoint_interval": -1}})

    def test_config_gepa_new_defaults(self):
        """Default Config should include valid defaults for new GEPAConfig fields."""
        config = Config(agent=AgentConfig(model_config=ModelConfig(provider="openai", model="test-model")))
        assert config.gepa.diversity_probability == 0.1
        assert config.gepa.patience == 10
        assert config.gepa.checkpoint_interval == 5


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
        with pytest.raises(ValueError, match="interval (must be positive|too large)"):
            OperatorConfig(interval=interval)


class TestOperatorConfigTimeoutsProperty:
    """Property-based tests for OperatorConfig timeout and iteration fields."""

    @given(st.data())
    @settings(max_examples=50, deadline=1000)
    def test_any_positive_timeout_accepted(self, data):
        """Any positive integer for a timeout/max-iters field is accepted and stored correctly."""
        field = data.draw(
            st.sampled_from(
                [
                    "agent_fix_timeout",
                    "deploy_timeout",
                    "agent_timeout",
                    "monitoring_max_iters",
                    "deployment_max_iters",
                ]
            )
        )
        value = data.draw(st.integers(min_value=1))
        config = OperatorConfig(**{field: value})
        assert getattr(config, field) == value

    @given(st.data())
    @settings(max_examples=50, deadline=1000)
    def test_any_non_positive_timeout_rejected(self, data):
        """Any non-positive integer for a timeout/max-iters field raises ValueError."""
        field = data.draw(
            st.sampled_from(
                [
                    "agent_fix_timeout",
                    "deploy_timeout",
                    "agent_timeout",
                    "monitoring_max_iters",
                    "deployment_max_iters",
                ]
            )
        )
        value = data.draw(st.integers(max_value=0))
        with pytest.raises(ValueError, match="must be positive"):
            OperatorConfig(**{field: value})


class TestConfigFromDictRoundTripProperty:
    """Property-based round-trip tests for Config.from_dict."""

    @given(
        provider=st.sampled_from(
            sorted(
                [
                    "codex",
                    "gemini",
                    "claude",
                    "claude-code",
                    "opencode",
                    "anthropic",
                    "vertex",
                    "openai",
                    "rlm",
                    "subagent",
                    "hybrid",
                ]
            )
        ),
        interval=st.integers(min_value=1, max_value=86400),
    )
    @settings(max_examples=50, deadline=1000)
    def test_valid_provider_and_interval_roundtrip(self, provider, interval):
        """Config built from a dict preserves provider and interval exactly."""
        config = Config.from_dict(
            {"agent": {"backend": provider, "model": "test-model"}, "operator": {"interval": interval}}
        )
        assert config.agent.backend == provider
        assert config.operator.interval == interval

    @given(
        section_name=st.text(min_size=1, max_size=30).filter(
            lambda s: s not in {"agent", "operator", "deployment", "runtime", "gepa", "dspy", "fault_injection"}
        ),
    )
    @settings(max_examples=50, deadline=1000)
    def test_unknown_section_always_rejected(self, section_name):
        """Any unknown top-level section key always raises an error."""
        with pytest.raises(Exception, match="(Unknown|Invalid|Unexpected|unexpected|unknown|invalid|Unrecognized)"):
            Config.from_dict({section_name: {}})

    @given(st.just({}))
    @settings(max_examples=1, deadline=1000)
    def test_default_config_from_empty_dict(self, data):
        """Config.from_dict({}) always produces defaults matching Config(agent=AgentConfig(...))."""
        default = Config(agent=AgentConfig(model_config=ModelConfig(provider="openai", model="m")))
        from_dict = Config.from_dict({"agent": {"model": "m"}})
        assert from_dict.agent.backend == default.agent.backend
        assert from_dict.operator.interval == default.operator.interval
