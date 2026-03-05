"""Tests for OperatorPhaseConfig."""

import pytest

from app_operator.config import Config, OperatorPhaseConfig, UnrecognizedFieldError


class TestOperatorPhaseConfig:
    """Test OperatorPhaseConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = OperatorPhaseConfig()
        assert config.code_analysis is True
        assert config.fix_summary_consolidation is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = OperatorPhaseConfig(code_analysis=False)
        assert config.code_analysis is False

    def test_custom_fix_summary_consolidation(self):
        """Test custom fix_summary_consolidation value."""
        config = OperatorPhaseConfig(fix_summary_consolidation=False)
        assert config.fix_summary_consolidation is False

    def test_type_validation_bool_required(self):
        """Test that code_analysis must be a bool."""
        with pytest.raises(TypeError, match="code_analysis must be bool"):
            OperatorPhaseConfig(code_analysis="true")

        with pytest.raises(TypeError, match="code_analysis must be bool"):
            OperatorPhaseConfig(code_analysis=1)

        with pytest.raises(TypeError, match="code_analysis must be bool"):
            OperatorPhaseConfig(code_analysis=None)

    def test_fix_summary_consolidation_type_validation(self):
        """Test that fix_summary_consolidation must be a bool."""
        with pytest.raises(TypeError, match="fix_summary_consolidation must be bool"):
            OperatorPhaseConfig(fix_summary_consolidation="true")

        with pytest.raises(TypeError, match="fix_summary_consolidation must be bool"):
            OperatorPhaseConfig(fix_summary_consolidation=1)

        with pytest.raises(TypeError, match="fix_summary_consolidation must be bool"):
            OperatorPhaseConfig(fix_summary_consolidation=None)


class TestOperatorPhaseConfigParsing:
    """Test parsing of operator.phase section from TOML."""

    def test_nested_phase_section_parsing(self):
        """Test parsing nested [operator.phase] section."""
        data = {
            "agent": {"provider": "codex", "model": "test-model"},
            "operator": {"interval": 60, "phase": {"code_analysis": False}},
        }
        config = Config.from_dict(data)
        assert config.operator.interval == 60
        assert config.operator.phase.code_analysis is False

    def test_nested_phase_fix_summary_consolidation_parsing(self):
        """Test parsing fix_summary_consolidation from nested [operator.phase] section."""
        data = {
            "agent": {"provider": "codex", "model": "test-model"},
            "operator": {"phase": {"fix_summary_consolidation": False}},
        }
        config = Config.from_dict(data)
        assert config.operator.phase.fix_summary_consolidation is False
        assert config.operator.phase.code_analysis is True  # Default

    def test_operator_without_phase_section(self):
        """Test that operator config works without phase section."""
        data = {"agent": {"provider": "codex", "model": "test-model"}, "operator": {"interval": 60}}
        config = Config.from_dict(data)
        assert config.operator.interval == 60
        assert config.operator.phase.code_analysis is True  # Default

    def test_empty_operator_section(self):
        """Test that empty operator section uses defaults."""
        data = {"agent": {"provider": "codex", "model": "test-model"}, "operator": {}}
        config = Config.from_dict(data)
        assert config.operator.interval == 30  # Default
        assert config.operator.phase.code_analysis is True  # Default

    def test_no_operator_section(self):
        """Test that missing operator section uses defaults."""
        data = {"agent": {"provider": "codex", "model": "test-model"}}
        config = Config.from_dict(data)
        assert config.operator.interval == 30  # Default
        assert config.operator.phase.code_analysis is True  # Default

    def test_phase_section_only(self):
        """Test that phase section can be specified alone."""
        data = {"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}}
        config = Config.from_dict(data)
        assert config.operator.interval == 30  # Default
        assert config.operator.phase.code_analysis is False

    def test_unrecognized_phase_field_error(self):
        """Test that unrecognized fields in phase section raise error."""
        data = {"operator": {"phase": {"code_analysis": False, "unknown_field": True}}}
        with pytest.raises(
            UnrecognizedFieldError, match=r"Unrecognized field\(s\) in \[operator\.phase\] section: unknown_field"
        ):
            Config.from_dict(data)

    def test_multiple_unrecognized_phase_fields(self):
        """Test error message with multiple unrecognized fields."""
        data = {"operator": {"phase": {"code_analysis": False, "foo": True, "bar": False}}}
        with pytest.raises(UnrecognizedFieldError, match=r"Unrecognized field\(s\) in \[operator\.phase\] section"):
            Config.from_dict(data)

    def test_phase_not_a_dict(self):
        """Test that phase must be a dict if present."""
        data = {"operator": {"phase": "not a dict"}}
        # Should raise an error when trying to create OperatorPhaseConfig
        with pytest.raises((TypeError, AttributeError)):
            Config.from_dict(data)

    def test_backward_compatibility(self):
        """Test that existing configs without phase section still work."""
        # Simulate old config file
        data = {
            "agent": {"provider": "codex", "model": "test-model"},
            "operator": {"interval": 30, "monitoring_max_iters": 5, "deployment_max_iters": 20},
        }
        config = Config.from_dict(data)
        assert config.operator.interval == 30
        assert config.operator.monitoring_max_iters == 5
        assert config.operator.deployment_max_iters == 20
        assert config.operator.phase.code_analysis is True  # Default

    def test_complete_config_with_phase(self):
        """Test complete config with all operator fields including phase."""
        data = {
            "agent": {"provider": "codex", "model": "test-model"},
            "operator": {
                "interval": 60,
                "monitoring_max_iters": 10,
                "deployment_max_iters": 30,
                "agent_fix_timeout": 3600,
                "deploy_timeout": 1800,
                "agent_timeout": 600,
                "phase": {"code_analysis": False},
            },
        }
        config = Config.from_dict(data)
        assert config.operator.interval == 60
        assert config.operator.monitoring_max_iters == 10
        assert config.operator.deployment_max_iters == 30
        assert config.operator.agent_fix_timeout == 3600
        assert config.operator.deploy_timeout == 1800
        assert config.operator.agent_timeout == 600
        assert config.operator.phase.code_analysis is False

    def test_phase_does_not_interfere_with_operator_validation(self):
        """Test that phase section doesn't affect operator field validation."""
        data = {
            "operator": {
                "interval": -1,  # Invalid
                "phase": {"code_analysis": False},
            }
        }
        with pytest.raises(ValueError, match="interval must be positive"):
            Config.from_dict(data)

    def test_git_integration_defaults_to_false(self):
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}})
        assert config.operator.phase.git_integration is False

    def test_git_integration_enabled_via_config(self):
        config = Config.from_dict(
            {"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"git_integration": True}}}
        )
        assert config.operator.phase.git_integration is True

    def test_git_integration_rejects_non_bool(self):
        with pytest.raises(TypeError):
            Config.from_dict({"operator": {"phase": {"git_integration": "yes"}}})
