"""Tests for fault injection configuration validation."""

import pytest

from app_operator.fault_injection.config import FaultInjectionConfig


class TestFaultInjectionConfig:
    def test_default_values(self):
        config = FaultInjectionConfig()
        assert config.enabled is False
        assert config.num_faults == 2
        assert config.categories == []
        assert config.severities == []
        assert config.exclude_faults == []
        assert config.seed is None
        assert config.backup_compose is True
        assert config.platform == "compose"

    def test_valid_config(self):
        config = FaultInjectionConfig(
            enabled=True,
            num_faults=3,
            categories=["misconfiguration", "security"],
            severities=["low", "medium"],
            exclude_faults=["MISC-001"],
            seed=42,
        )
        assert config.enabled is True
        assert config.num_faults == 3
        assert config.seed == 42

    # --- enabled ---
    def test_invalid_enabled_type(self):
        with pytest.raises(TypeError, match="enabled must be bool"):
            FaultInjectionConfig(enabled="true")

    # --- num_faults ---
    def test_invalid_num_faults_type(self):
        with pytest.raises(TypeError, match="num_faults must be int"):
            FaultInjectionConfig(num_faults="2")

    def test_num_faults_too_low(self):
        with pytest.raises(ValueError, match="num_faults must be between 1 and 5"):
            FaultInjectionConfig(num_faults=0)

    def test_num_faults_too_high(self):
        with pytest.raises(ValueError, match="num_faults must be between 1 and 5"):
            FaultInjectionConfig(num_faults=6)

    def test_num_faults_boundary(self):
        config = FaultInjectionConfig(num_faults=1)
        assert config.num_faults == 1
        config = FaultInjectionConfig(num_faults=5)
        assert config.num_faults == 5

    # --- categories ---
    def test_invalid_categories_type(self):
        with pytest.raises(TypeError, match="categories must be list"):
            FaultInjectionConfig(categories="misconfiguration")

    def test_invalid_category_value(self):
        with pytest.raises(ValueError, match="Invalid category 'invalid'"):
            FaultInjectionConfig(categories=["invalid"])

    def test_valid_all_categories(self):
        config = FaultInjectionConfig(
            categories=[
                "misconfiguration", "security", "metastable",
                "correlated", "infrastructure",
            ]
        )
        assert len(config.categories) == 5

    # --- severities ---
    def test_invalid_severities_type(self):
        with pytest.raises(TypeError, match="severities must be list"):
            FaultInjectionConfig(severities="low")

    def test_invalid_severity_value(self):
        with pytest.raises(ValueError, match="Invalid severity 'invalid'"):
            FaultInjectionConfig(severities=["invalid"])

    def test_valid_all_severities(self):
        config = FaultInjectionConfig(
            severities=["low", "medium", "high", "critical"]
        )
        assert len(config.severities) == 4

    # --- exclude_faults ---
    def test_invalid_exclude_faults_type(self):
        with pytest.raises(TypeError, match="exclude_faults must be list"):
            FaultInjectionConfig(exclude_faults="MISC-001")

    # --- seed ---
    def test_invalid_seed_type(self):
        with pytest.raises(TypeError, match="seed must be int or None"):
            FaultInjectionConfig(seed="42")

    def test_seed_none(self):
        config = FaultInjectionConfig(seed=None)
        assert config.seed is None

    # --- backup_compose ---
    def test_invalid_backup_compose_type(self):
        with pytest.raises(TypeError, match="backup_compose must be bool"):
            FaultInjectionConfig(backup_compose="true")

    # --- platform ---
    def test_invalid_platform_type(self):
        with pytest.raises(TypeError, match="platform must be str"):
            FaultInjectionConfig(platform=123)

    def test_invalid_platform_value(self):
        with pytest.raises(ValueError, match="Invalid platform 'invalid'"):
            FaultInjectionConfig(platform="invalid")

    def test_valid_platforms(self):
        config = FaultInjectionConfig(platform="compose")
        assert config.platform == "compose"
        config = FaultInjectionConfig(platform="k8s")
        assert config.platform == "k8s"
