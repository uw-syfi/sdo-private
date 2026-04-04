"""Unit tests for the triage Pydantic models."""

from __future__ import annotations

from sregym_agents.crucible.tools import (
    TriageAnomaly,
    TriageReport,
    format_triage_report,
)


class TestTriageReport:
    def test_roundtrip(self) -> None:
        anomaly = TriageAnomaly(
            category="Non-Running Pods",
            resource_kind="Pod",
            resource_name="frontend-abc",
            namespace="default",
            observation="CrashLoopBackOff with 5 restarts, Exit Code 137",
        )
        report = TriageReport(
            anomalies=[anomaly],
            raw_cluster_snapshot="kubectl get pods -A output...",
        )
        json_str = report.model_dump_json()
        restored = TriageReport.model_validate_json(json_str)
        assert restored.anomalies[0].resource_name == "frontend-abc"
        assert restored.anomalies[0].category == "Non-Running Pods"
        assert restored.raw_cluster_snapshot == "kubectl get pods -A output..."

    def test_empty_report(self) -> None:
        report = TriageReport()
        assert report.anomalies == []
        assert report.raw_cluster_snapshot == ""

    def test_multiple_categories(self) -> None:
        report = TriageReport(
            anomalies=[
                TriageAnomaly(
                    category="Non-Running Pods", resource_kind="Pod", resource_name="a", namespace="ns", observation="x"
                ),
                TriageAnomaly(
                    category="Services Without Endpoints",
                    resource_kind="Service",
                    resource_name="b",
                    namespace="ns",
                    observation="y",
                ),
                TriageAnomaly(
                    category="ConfigMap Anomalies",
                    resource_kind="ConfigMap",
                    resource_name="c",
                    namespace="ns",
                    observation="z",
                ),
                TriageAnomaly(
                    category="Non-Running Pods", resource_kind="Pod", resource_name="d", namespace="ns", observation="w"
                ),
            ],
        )
        json_str = report.model_dump_json()
        restored = TriageReport.model_validate_json(json_str)
        assert len(restored.anomalies) == 4
        pods = [a for a in restored.anomalies if a.category == "Non-Running Pods"]
        assert len(pods) == 2

    def test_format_groups_by_category(self) -> None:
        report = TriageReport(
            anomalies=[
                TriageAnomaly(
                    category="Non-Running Pods", resource_kind="Pod", resource_name="a", namespace="ns", observation="x"
                ),
                TriageAnomaly(
                    category="Port Mismatch",
                    resource_kind="Service",
                    resource_name="b",
                    namespace="ns",
                    observation="y",
                ),
                TriageAnomaly(
                    category="Non-Running Pods", resource_kind="Pod", resource_name="c", namespace="ns", observation="z"
                ),
            ],
        )
        md = format_triage_report(report)
        assert "**Non-Running Pods**" in md
        assert "**Port Mismatch**" in md
        # Both pods appear under the same heading
        assert md.index("**Non-Running Pods**") < md.index("`Pod/c`")

    def test_format_empty_report(self) -> None:
        md = format_triage_report(TriageReport())
        assert "No anomalies detected." in md
