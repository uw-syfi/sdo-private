"""Tests for fault injection models."""

import pytest

from app_operator.fault_injection.models import (
    Fault,
    FaultCategory,
    FaultResult,
    FaultSeverity,
)


class TestFaultCategory:
    def test_values(self):
        assert FaultCategory.MISCONFIGURATION == "misconfiguration"
        assert FaultCategory.SECURITY == "security"
        assert FaultCategory.METASTABLE == "metastable"
        assert FaultCategory.CORRELATED == "correlated"
        assert FaultCategory.INFRASTRUCTURE == "infrastructure"

    def test_string_comparison(self):
        assert FaultCategory.MISCONFIGURATION == "misconfiguration"
        assert FaultCategory.SECURITY == "security"


class TestFaultSeverity:
    def test_values(self):
        assert FaultSeverity.LOW == "low"
        assert FaultSeverity.MEDIUM == "medium"
        assert FaultSeverity.HIGH == "high"
        assert FaultSeverity.CRITICAL == "critical"


class TestFault:
    def test_creation(self):
        fault = Fault(
            fault_id="TEST-001",
            name="test_fault",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="A test fault",
        )
        assert fault.fault_id == "TEST-001"
        assert fault.name == "test_fault"
        assert fault.applicable_services == ()
        assert fault.platform == "compose"

    def test_frozen(self):
        fault = Fault(
            fault_id="TEST-001",
            name="test_fault",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="A test fault",
        )
        with pytest.raises(AttributeError):
            fault.name = "modified"  # type: ignore[misc]

    def test_with_applicable_services(self):
        fault = Fault(
            fault_id="TEST-002",
            name="backend_fault",
            category=FaultCategory.CORRELATED,
            severity=FaultSeverity.HIGH,
            description="Targets backends",
            applicable_services=["backend"],  # type: ignore[arg-type]
        )
        assert fault.applicable_services == ["backend"]


class TestFaultResult:
    def test_success_result(self):
        fault = Fault(
            fault_id="TEST-001",
            name="test_fault",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="A test fault",
        )
        result = FaultResult(
            fault=fault,
            target_service="frontend",
            modified_fields={"ports[0]": {"old": "8080:80", "new": "45000:80"}},
        )
        assert result.success is True
        assert result.error_message is None

    def test_failed_result(self):
        fault = Fault(
            fault_id="TEST-001",
            name="test_fault",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="A test fault",
        )
        result = FaultResult(
            fault=fault,
            target_service="frontend",
            success=False,
            error_message="No ports found",
        )
        assert result.success is False
        assert result.error_message == "No ports found"

    def test_to_dict(self):
        fault = Fault(
            fault_id="TEST-001",
            name="test_fault",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="A test fault",
        )
        result = FaultResult(
            fault=fault,
            target_service="frontend",
            modified_fields={"key": "value"},
        )
        d = result.to_dict()
        assert d["fault_id"] == "TEST-001"
        assert d["fault_name"] == "test_fault"
        assert d["category"] == "misconfiguration"
        assert d["severity"] == "low"
        assert d["target_service"] == "frontend"
        assert d["modified_fields"] == {"key": "value"}
        assert d["success"] is True
        assert d["error_message"] is None
