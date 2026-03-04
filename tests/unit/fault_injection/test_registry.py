"""Tests for fault registry."""

import random

import pytest

from app_operator.fault_injection.compose_faults import COMPOSE_FAULTS
from app_operator.fault_injection.config import FaultInjectionConfig
from app_operator.fault_injection.models import FaultCategory, FaultSeverity
from app_operator.fault_injection.registry import FaultRegistry


@pytest.fixture
def registry():
    return FaultRegistry(COMPOSE_FAULTS)


class TestFaultRegistry:
    def test_all_faults(self, registry):
        assert len(registry.all_faults) == 22

    def test_get_by_id(self, registry):
        fault = registry.get("MISC-001")
        assert fault is not None
        assert fault.name == "wrong_port_mapping"

    def test_get_missing(self, registry):
        assert registry.get("NONEXISTENT") is None

    def test_filter_by_category(self, registry):
        misconfig = registry.filter(categories=["misconfiguration"])
        assert len(misconfig) == 6
        assert all(f.category == FaultCategory.MISCONFIGURATION for f in misconfig)

    def test_filter_by_severity(self, registry):
        low = registry.filter(severities=["low"])
        assert all(f.severity == FaultSeverity.LOW for f in low)
        assert len(low) > 0

    def test_filter_by_multiple_categories(self, registry):
        result = registry.filter(categories=["security", "infrastructure"])
        assert all(
            f.category in (FaultCategory.SECURITY, FaultCategory.INFRASTRUCTURE)
            for f in result
        )

    def test_filter_by_platform(self, registry):
        compose = registry.filter(platform="compose")
        assert len(compose) == 22  # All are compose faults

    def test_filter_exclude(self, registry):
        result = registry.filter(exclude=["MISC-001", "MISC-002"])
        ids = {f.fault_id for f in result}
        assert "MISC-001" not in ids
        assert "MISC-002" not in ids

    def test_filter_combined(self, registry):
        result = registry.filter(
            categories=["misconfiguration"],
            severities=["low"],
            exclude=["MISC-001"],
        )
        # MISC-001 is the only low-severity misconfiguration, after excluding it
        assert len(result) == 0

    def test_select_n_faults(self, registry):
        rng = random.Random(42)
        selected = registry.select(3, rng=rng)
        assert len(selected) == 3

    def test_select_with_config(self, registry):
        config = FaultInjectionConfig(
            categories=["misconfiguration"],
            severities=["medium"],
        )
        rng = random.Random(42)
        selected = registry.select(2, config=config, rng=rng)
        assert len(selected) == 2
        assert all(f.category == FaultCategory.MISCONFIGURATION for f in selected)
        assert all(f.severity == FaultSeverity.MEDIUM for f in selected)

    def test_select_more_than_available(self, registry):
        config = FaultInjectionConfig(
            categories=["misconfiguration"],
            severities=["low"],
        )
        rng = random.Random(42)
        selected = registry.select(10, config=config, rng=rng)
        # Only 1 low-severity misconfiguration fault
        assert len(selected) == 1

    def test_select_reproducible(self, registry):
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        s1 = registry.select(3, rng=rng1)
        s2 = registry.select(3, rng=rng2)
        assert [f.fault_id for f in s1] == [f.fault_id for f in s2]

    def test_select_empty_candidates(self, registry):
        config = FaultInjectionConfig(
            categories=["misconfiguration"],
            severities=["critical"],  # No critical misconfiguration faults
        )
        rng = random.Random(42)
        selected = registry.select(2, config=config, rng=rng)
        assert len(selected) == 0
