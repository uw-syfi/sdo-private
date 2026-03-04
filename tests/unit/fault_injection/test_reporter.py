"""Tests for fault report / trajectory metadata bridge."""

from app_operator.fault_injection.models import (
    Fault,
    FaultCategory,
    FaultResult,
    FaultSeverity,
)
from app_operator.fault_injection.reporter import FaultReport


class TestFaultReport:
    def test_to_trajectory_metadata_success(self):
        fault1 = Fault(
            fault_id="MISC-001", name="wrong_port",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW, description="d",
        )
        fault2 = Fault(
            fault_id="SEC-001", name="removed_auth",
            category=FaultCategory.SECURITY,
            severity=FaultSeverity.MEDIUM, description="d",
        )
        results = [
            FaultResult(fault=fault1, target_service="frontend", success=True,
                        modified_fields={"ports": "changed"}),
            FaultResult(fault=fault2, target_service="geo", success=True,
                        modified_fields={"env": "removed"}),
        ]
        meta = FaultReport.to_trajectory_metadata(results)
        assert meta["enabled"] is True
        assert meta["num_faults_requested"] == 2
        assert meta["num_faults_injected"] == 2
        assert len(meta["faults"]) == 2
        assert meta["fault_ids"] == ["MISC-001", "SEC-001"]
        assert "misconfiguration" in meta["categories"]
        assert "security" in meta["categories"]
        assert "low" in meta["severities"]
        assert "medium" in meta["severities"]

    def test_to_trajectory_metadata_with_failures(self):
        fault = Fault(
            fault_id="MISC-002", name="missing_env",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.MEDIUM, description="d",
        )
        results = [
            FaultResult(fault=fault, target_service="svc", success=False,
                        error_message="No env vars"),
        ]
        meta = FaultReport.to_trajectory_metadata(results)
        assert meta["num_faults_injected"] == 0
        assert len(meta["failed_injections"]) == 1
        assert meta["fault_ids"] == []

    def test_to_trajectory_metadata_empty(self):
        meta = FaultReport.to_trajectory_metadata([])
        assert meta["num_faults_requested"] == 0
        assert meta["num_faults_injected"] == 0
        assert meta["faults"] == []
