"""Tests for DSPy config integration with main Config class."""

import pytest
from app_operator.config import Config, UnrecognizedFieldError
from app_operator.dspy_integration.config import (
    DSPyConfig,
    DSPyOptimizationConfig,
    DSPyAutoRollbackConfig,
)


class TestConfigIntegration:
    """Tests for DSPy integration with Config class."""

    def test_default_config_includes_dspy(self):
        """Test that default Config includes DSPy config."""
        config = Config()
        assert isinstance(config.dspy, DSPyConfig)
        assert config.dspy.use_optimized is False
        assert config.dspy.optimized_version == "latest"

    def test_from_dict_empty_dspy_section(self):
        """Test loading config with empty dspy section."""
        config = Config.from_dict({"dspy": {}})
        assert isinstance(config.dspy, DSPyConfig)
        assert config.dspy.use_optimized is False

    def test_from_dict_with_dspy_fields(self):
        """Test loading config with dspy fields."""
        data = {
            "dspy": {
                "use_optimized": True,
                "optimized_version": "v2",
                "fallback_to_baseline": False,
                "enable_online_learning": True,
                "feedback_sample_rate": 0.2,
            }
        }
        config = Config.from_dict(data)
        assert config.dspy.use_optimized is True
        assert config.dspy.optimized_version == "v2"
        assert config.dspy.fallback_to_baseline is False
        assert config.dspy.enable_online_learning is True
        assert config.dspy.feedback_sample_rate == 0.2

    def test_from_dict_with_nested_optimization(self):
        """Test loading config with nested optimization section."""
        data = {
            "dspy": {
                "optimization": {
                    "optimizer": "MIPROv2",
                    "teacher_model": "claude-opus-4-5",
                    "num_examples": 50,
                    "validation_split": 0.3,
                }
            }
        }
        config = Config.from_dict(data)
        assert isinstance(config.dspy.optimization, DSPyOptimizationConfig)
        assert config.dspy.optimization.optimizer == "MIPROv2"
        assert config.dspy.optimization.teacher_model == "claude-opus-4-5"
        assert config.dspy.optimization.num_examples == 50
        assert config.dspy.optimization.validation_split == 0.3

    def test_from_dict_with_nested_auto_rollback(self):
        """Test loading config with nested auto_rollback section."""
        data = {
            "dspy": {
                "auto_rollback": {
                    "enabled": False,
                    "success_rate_threshold": 0.1,
                    "evaluation_window": 200,
                }
            }
        }
        config = Config.from_dict(data)
        assert isinstance(config.dspy.auto_rollback, DSPyAutoRollbackConfig)
        assert config.dspy.auto_rollback.enabled is False
        assert config.dspy.auto_rollback.success_rate_threshold == 0.1
        assert config.dspy.auto_rollback.evaluation_window == 200

    def test_from_dict_with_metric_weights(self):
        """Test loading config with custom metric weights."""
        data = {
            "dspy": {
                "optimization": {
                    "metric_weights": {
                        "success": 0.5,
                        "efficiency": 0.3,
                        "tokens": 0.2,
                    }
                }
            }
        }
        config = Config.from_dict(data)
        assert config.dspy.optimization.metric_weights == {
            "success": 0.5,
            "efficiency": 0.3,
            "tokens": 0.2,
        }

    def test_from_dict_with_all_sections(self):
        """Test loading config with all sections including dspy."""
        data = {
            "agent": {"provider": "claude"},
            "operator": {"interval": 60},
            "deployment": {"platform": "docker"},
            "runtime": {"impl": "cli_agent"},
            "dspy": {
                "use_optimized": True,
                "optimization": {
                    "num_examples": 40,
                },
                "auto_rollback": {
                    "enabled": True,
                },
            },
        }
        config = Config.from_dict(data)
        assert config.agent.provider == "claude"
        assert config.operator.interval == 60
        assert config.deployment.platform == "docker"
        assert config.runtime.impl == "cli_agent"
        assert config.dspy.use_optimized is True
        assert config.dspy.optimization.num_examples == 40
        assert config.dspy.auto_rollback.enabled is True

    def test_unrecognized_dspy_field(self):
        """Test unrecognized field in dspy section raises error."""
        data = {
            "dspy": {
                "use_optimized": True,
                "invalid_field": "value",
            }
        }
        with pytest.raises(UnrecognizedFieldError, match="Unrecognized field.*invalid_field"):
            Config.from_dict(data)

    def test_unrecognized_optimization_field(self):
        """Test unrecognized field in optimization section raises error."""
        data = {
            "dspy": {
                "optimization": {
                    "optimizer": "BootstrapFewShot",
                    "invalid_optimizer_field": "value",
                }
            }
        }
        with pytest.raises(UnrecognizedFieldError, match="Unrecognized field.*invalid_optimizer_field"):
            Config.from_dict(data)

    def test_unrecognized_auto_rollback_field(self):
        """Test unrecognized field in auto_rollback section raises error."""
        data = {
            "dspy": {
                "auto_rollback": {
                    "enabled": True,
                    "invalid_rollback_field": "value",
                }
            }
        }
        with pytest.raises(UnrecognizedFieldError, match="Unrecognized field.*invalid_rollback_field"):
            Config.from_dict(data)

    def test_invalid_dspy_value_raises_error(self):
        """Test invalid dspy value raises appropriate error."""
        data = {
            "dspy": {
                "use_optimized": "not_a_boolean",
            }
        }
        with pytest.raises(TypeError, match="use_optimized must be bool"):
            Config.from_dict(data)

    def test_invalid_optimization_value_raises_error(self):
        """Test invalid optimization value raises appropriate error."""
        data = {
            "dspy": {
                "optimization": {
                    "num_examples": -10,
                }
            }
        }
        with pytest.raises(ValueError, match="num_examples must be positive"):
            Config.from_dict(data)

    def test_canary_deployment_validation(self):
        """Test canary deployment validation in Config."""
        data = {
            "dspy": {
                "use_optimized": False,
                "canary_deployment": True,
            }
        }
        with pytest.raises(ValueError, match="canary_deployment requires use_optimized=true"):
            Config.from_dict(data)
