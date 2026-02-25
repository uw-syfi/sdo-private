"""Tests for DSPy configuration validation."""

import pytest
from app_operator.dspy_integration.config import (
    DSPyConfig,
    DSPyOptimizationConfig,
    DSPyAutoRollbackConfig,
)


class TestDSPyOptimizationConfig:
    """Tests for DSPyOptimizationConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DSPyOptimizationConfig()
        assert config.optimizer == "BootstrapFewShot"
        assert config.teacher_model == "claude-sonnet-4-5"
        assert config.num_examples == 30
        assert config.validation_split == 0.2
        assert config.metric_weights == {
            "success": 0.5,
            "efficiency": 0.25,
            "tokens": 0.15,
            "health_check": 0.1,
        }

    def test_valid_optimizer(self):
        """Test valid optimizer configurations."""
        valid_optimizers = [
            "BootstrapFewShot",
            "BootstrapFewShotWithRandomSearch",
            "MIPROv2",
            "COPRO",
        ]
        for optimizer in valid_optimizers:
            config = DSPyOptimizationConfig(optimizer=optimizer)
            assert config.optimizer == optimizer

    def test_invalid_optimizer(self):
        """Test invalid optimizer raises ValueError."""
        with pytest.raises(ValueError, match="optimizer must be one of"):
            DSPyOptimizationConfig(optimizer="InvalidOptimizer")

    def test_invalid_teacher_model(self):
        """Test invalid teacher_model raises ValueError."""
        with pytest.raises(ValueError, match="teacher_model must be a non-empty string"):
            DSPyOptimizationConfig(teacher_model="")

        with pytest.raises(ValueError, match="teacher_model must be a non-empty string"):
            DSPyOptimizationConfig(teacher_model="   ")

    def test_invalid_num_examples_type(self):
        """Test invalid num_examples type raises TypeError."""
        with pytest.raises(TypeError, match="num_examples must be int"):
            DSPyOptimizationConfig(num_examples="30")

    def test_invalid_num_examples_value(self):
        """Test invalid num_examples value raises ValueError."""
        with pytest.raises(ValueError, match="num_examples must be positive"):
            DSPyOptimizationConfig(num_examples=0)

        with pytest.raises(ValueError, match="num_examples must be positive"):
            DSPyOptimizationConfig(num_examples=-10)

    def test_invalid_validation_split_type(self):
        """Test invalid validation_split type raises TypeError."""
        with pytest.raises(TypeError, match="validation_split must be numeric"):
            DSPyOptimizationConfig(validation_split="0.2")

    def test_invalid_validation_split_range(self):
        """Test validation_split out of range raises ValueError."""
        with pytest.raises(ValueError, match="validation_split must be in range"):
            DSPyOptimizationConfig(validation_split=-0.1)

        with pytest.raises(ValueError, match="validation_split must be in range"):
            DSPyOptimizationConfig(validation_split=1.0)

        with pytest.raises(ValueError, match="validation_split must be in range"):
            DSPyOptimizationConfig(validation_split=1.5)

    def test_valid_validation_split_boundary(self):
        """Test validation_split boundary values."""
        config = DSPyOptimizationConfig(validation_split=0.0)
        assert config.validation_split == 0.0

        config = DSPyOptimizationConfig(validation_split=0.99)
        assert config.validation_split == 0.99

    def test_invalid_metric_weights_type(self):
        """Test invalid metric_weights type raises TypeError."""
        with pytest.raises(TypeError, match="metric_weights must be dict"):
            DSPyOptimizationConfig(metric_weights=[0.6, 0.25, 0.15])

    def test_invalid_metric_weights_keys(self):
        """Test invalid metric_weights keys raises ValueError."""
        with pytest.raises(ValueError, match="metric_weights must contain either"):
            DSPyOptimizationConfig(metric_weights={"success": 0.6, "efficiency": 0.4})

        with pytest.raises(ValueError, match="metric_weights must contain"):
            DSPyOptimizationConfig(
                metric_weights={
                    "success": 0.5,
                    "efficiency": 0.3,
                    "tokens": 0.1,
                    "extra": 0.1,
                }
            )

    def test_invalid_metric_weight_value_type(self):
        """Test invalid metric weight value type raises TypeError."""
        with pytest.raises(TypeError, match="metric_weights\\['success'\\] must be numeric"):
            DSPyOptimizationConfig(
                metric_weights={"success": "0.6", "efficiency": 0.25, "tokens": 0.15}
            )

    def test_invalid_metric_weight_value_range(self):
        """Test metric weight out of range raises ValueError."""
        with pytest.raises(ValueError, match="metric_weights\\['success'\\] must be in range"):
            DSPyOptimizationConfig(
                metric_weights={"success": -0.1, "efficiency": 0.6, "tokens": 0.5}
            )

        with pytest.raises(ValueError, match="metric_weights\\['efficiency'\\] must be in range"):
            DSPyOptimizationConfig(
                metric_weights={"success": 0.5, "efficiency": 1.5, "tokens": 0.0}
            )

    def test_metric_weights_sum_validation(self):
        """Test metric weights must sum to 1.0."""
        with pytest.raises(ValueError, match="metric_weights must sum to 1.0"):
            DSPyOptimizationConfig(
                metric_weights={"success": 0.5, "efficiency": 0.3, "tokens": 0.1}
            )

        with pytest.raises(ValueError, match="metric_weights must sum to 1.0"):
            DSPyOptimizationConfig(
                metric_weights={"success": 0.7, "efficiency": 0.3, "tokens": 0.2}
            )

    def test_metric_weights_sum_tolerance(self):
        """Test metric weights sum allows small floating point error."""
        # Should pass with small floating point error
        config = DSPyOptimizationConfig(
            metric_weights={
                "success": 0.6,
                "efficiency": 0.25,
                "tokens": 0.1500000001,
            }
        )
        assert config is not None


class TestDSPyAutoRollbackConfig:
    """Tests for DSPyAutoRollbackConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DSPyAutoRollbackConfig()
        assert config.enabled is True
        assert config.success_rate_threshold == 0.05
        assert config.evaluation_window == 100

    def test_invalid_enabled_type(self):
        """Test invalid enabled type raises TypeError."""
        with pytest.raises(TypeError, match="enabled must be bool"):
            DSPyAutoRollbackConfig(enabled="true")

    def test_invalid_success_rate_threshold_type(self):
        """Test invalid success_rate_threshold type raises TypeError."""
        with pytest.raises(TypeError, match="success_rate_threshold must be numeric"):
            DSPyAutoRollbackConfig(success_rate_threshold="0.05")

    def test_invalid_success_rate_threshold_range(self):
        """Test success_rate_threshold out of range raises ValueError."""
        with pytest.raises(ValueError, match="success_rate_threshold must be in range"):
            DSPyAutoRollbackConfig(success_rate_threshold=-0.1)

        with pytest.raises(ValueError, match="success_rate_threshold must be in range"):
            DSPyAutoRollbackConfig(success_rate_threshold=1.5)

    def test_valid_success_rate_threshold_boundary(self):
        """Test success_rate_threshold boundary values."""
        config = DSPyAutoRollbackConfig(success_rate_threshold=0.0)
        assert config.success_rate_threshold == 0.0

        config = DSPyAutoRollbackConfig(success_rate_threshold=1.0)
        assert config.success_rate_threshold == 1.0

    def test_invalid_evaluation_window_type(self):
        """Test invalid evaluation_window type raises TypeError."""
        with pytest.raises(TypeError, match="evaluation_window must be int"):
            DSPyAutoRollbackConfig(evaluation_window="100")

    def test_invalid_evaluation_window_value(self):
        """Test invalid evaluation_window value raises ValueError."""
        with pytest.raises(ValueError, match="evaluation_window must be positive"):
            DSPyAutoRollbackConfig(evaluation_window=0)

        with pytest.raises(ValueError, match="evaluation_window must be positive"):
            DSPyAutoRollbackConfig(evaluation_window=-50)


class TestDSPyConfig:
    """Tests for DSPyConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DSPyConfig()
        assert config.use_optimized is False
        assert config.optimized_version == "latest"
        assert config.fallback_to_baseline is True
        assert config.enable_online_learning is False
        assert config.feedback_sample_rate == 0.1
        assert config.canary_deployment is False
        assert config.canary_percentage == 0.0
        assert isinstance(config.optimization, DSPyOptimizationConfig)
        assert isinstance(config.auto_rollback, DSPyAutoRollbackConfig)

    def test_invalid_use_optimized_type(self):
        """Test invalid use_optimized type raises TypeError."""
        with pytest.raises(TypeError, match="use_optimized must be bool"):
            DSPyConfig(use_optimized="true")

    def test_invalid_optimized_version(self):
        """Test invalid optimized_version raises ValueError."""
        with pytest.raises(ValueError, match="optimized_version must be a non-empty string"):
            DSPyConfig(optimized_version="")

        with pytest.raises(ValueError, match="optimized_version must be a non-empty string"):
            DSPyConfig(optimized_version="   ")

    def test_invalid_fallback_to_baseline_type(self):
        """Test invalid fallback_to_baseline type raises TypeError."""
        with pytest.raises(TypeError, match="fallback_to_baseline must be bool"):
            DSPyConfig(fallback_to_baseline="true")

    def test_invalid_enable_online_learning_type(self):
        """Test invalid enable_online_learning type raises TypeError."""
        with pytest.raises(TypeError, match="enable_online_learning must be bool"):
            DSPyConfig(enable_online_learning="false")

    def test_invalid_feedback_sample_rate_type(self):
        """Test invalid feedback_sample_rate type raises TypeError."""
        with pytest.raises(TypeError, match="feedback_sample_rate must be numeric"):
            DSPyConfig(feedback_sample_rate="0.1")

    def test_invalid_feedback_sample_rate_range(self):
        """Test feedback_sample_rate out of range raises ValueError."""
        with pytest.raises(ValueError, match="feedback_sample_rate must be in range"):
            DSPyConfig(feedback_sample_rate=-0.1)

        with pytest.raises(ValueError, match="feedback_sample_rate must be in range"):
            DSPyConfig(feedback_sample_rate=1.5)

    def test_valid_feedback_sample_rate_boundary(self):
        """Test feedback_sample_rate boundary values."""
        config = DSPyConfig(feedback_sample_rate=0.0)
        assert config.feedback_sample_rate == 0.0

        config = DSPyConfig(feedback_sample_rate=1.0)
        assert config.feedback_sample_rate == 1.0

    def test_invalid_canary_deployment_type(self):
        """Test invalid canary_deployment type raises TypeError."""
        with pytest.raises(TypeError, match="canary_deployment must be bool"):
            DSPyConfig(canary_deployment="true")

    def test_invalid_canary_percentage_type(self):
        """Test invalid canary_percentage type raises TypeError."""
        with pytest.raises(TypeError, match="canary_percentage must be numeric"):
            DSPyConfig(canary_percentage="0.5")

    def test_invalid_canary_percentage_range(self):
        """Test canary_percentage out of range raises ValueError."""
        with pytest.raises(ValueError, match="canary_percentage must be in range"):
            DSPyConfig(canary_percentage=-0.1)

        with pytest.raises(ValueError, match="canary_percentage must be in range"):
            DSPyConfig(canary_percentage=1.5)

    def test_valid_canary_percentage_boundary(self):
        """Test canary_percentage boundary values."""
        config = DSPyConfig(use_optimized=True, canary_deployment=True, canary_percentage=0.0)
        assert config.canary_percentage == 0.0

        config = DSPyConfig(use_optimized=True, canary_deployment=True, canary_percentage=1.0)
        assert config.canary_percentage == 1.0

    def test_invalid_optimization_type(self):
        """Test invalid optimization type raises TypeError."""
        with pytest.raises(TypeError, match="optimization must be DSPyOptimizationConfig"):
            DSPyConfig(optimization={"optimizer": "BootstrapFewShot"})

    def test_invalid_auto_rollback_type(self):
        """Test invalid auto_rollback type raises TypeError."""
        with pytest.raises(TypeError, match="auto_rollback must be DSPyAutoRollbackConfig"):
            DSPyConfig(auto_rollback={"enabled": True})

    def test_canary_deployment_requires_use_optimized(self):
        """Test canary_deployment requires use_optimized=true."""
        with pytest.raises(ValueError, match="canary_deployment requires use_optimized=true"):
            DSPyConfig(use_optimized=False, canary_deployment=True)

    def test_valid_canary_deployment(self):
        """Test valid canary deployment configuration."""
        config = DSPyConfig(
            use_optimized=True,
            canary_deployment=True,
            canary_percentage=0.3,
        )
        assert config.use_optimized is True
        assert config.canary_deployment is True
        assert config.canary_percentage == 0.3

    def test_nested_config_validation(self):
        """Test nested config objects are validated."""
        # This should raise from the nested DSPyOptimizationConfig
        with pytest.raises(ValueError, match="num_examples must be positive"):
            DSPyConfig(
                optimization=DSPyOptimizationConfig(num_examples=-5)
            )

        # This should raise from the nested DSPyAutoRollbackConfig
        with pytest.raises(ValueError, match="evaluation_window must be positive"):
            DSPyConfig(
                auto_rollback=DSPyAutoRollbackConfig(evaluation_window=0)
            )
