"""Unit tests for the triage Pydantic models."""

from __future__ import annotations

from sregym_agents.crucible.tools import (
    TriageAnomaly,
    TriageReport,
)


class TestTriageReport:
    def test_roundtrip(self) -> None:
        anomaly = TriageAnomaly(
            resource_kind="Pod",
            resource_name="frontend-abc",
            namespace="default",
            observation="CrashLoopBackOff with 5 restarts, Exit Code 137",
        )
        report = TriageReport(
            non_running_pods=[anomaly],
            raw_cluster_snapshot="kubectl get pods -A output...",
        )
        json_str = report.model_dump_json()
        restored = TriageReport.model_validate_json(json_str)
        assert restored.non_running_pods[0].resource_name == "frontend-abc"
        assert restored.raw_cluster_snapshot == "kubectl get pods -A output..."

    def test_empty_report(self) -> None:
        report = TriageReport()
        assert report.non_running_pods == []
        assert report.raw_cluster_snapshot == ""

    def test_all_categories(self) -> None:
        report = TriageReport(
            non_running_pods=[TriageAnomaly(resource_kind="Pod", resource_name="a", namespace="ns", observation="x")],
            services_without_endpoints=[
                TriageAnomaly(resource_kind="Service", resource_name="b", namespace="ns", observation="y")
            ],
            configmap_anomalies=[
                TriageAnomaly(resource_kind="ConfigMap", resource_name="c", namespace="ns", observation="z")
            ],
            job_anomalies=[TriageAnomaly(resource_kind="Job", resource_name="d", namespace="ns", observation="w")],
        )
        json_str = report.model_dump_json()
        restored = TriageReport.model_validate_json(json_str)
        assert len(restored.non_running_pods) == 1
        assert len(restored.services_without_endpoints) == 1
        assert len(restored.configmap_anomalies) == 1
        assert len(restored.job_anomalies) == 1
