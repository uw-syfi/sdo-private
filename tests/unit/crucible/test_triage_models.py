"""Unit tests for the triage Pydantic models."""

from __future__ import annotations

import pytest

from sregym_agents.crucible.tools import (
    AreaAssessment,
    TriageAnomaly,
    TriageArea,
    TriagePriors,
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
        )
        json_str = report.model_dump_json()
        restored = TriageReport.model_validate_json(json_str)
        assert restored.anomalies[0].resource_name == "frontend-abc"
        assert restored.anomalies[0].category == "Non-Running Pods"

    def test_empty_report(self) -> None:
        report = TriageReport()
        assert report.anomalies == []
        assert report.area_assessments == []

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

    def test_format_with_anomalies(self) -> None:
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
            ],
        )
        md = format_triage_report(report)
        assert "**[Non-Running Pods]**" in md
        assert "**[Port Mismatch]**" in md
        assert "`Pod/a`" in md

    def test_format_with_area_assessments(self) -> None:
        report = TriageReport(
            area_assessments=[
                AreaAssessment(category="Network", assessment="DNS resolution failing."),
            ],
            anomalies=[
                TriageAnomaly(
                    category="Network", resource_kind="Pod", resource_name="a", namespace="ns", observation="x"
                ),
            ],
        )
        md = format_triage_report(report)
        assert "## Area Assessments" in md
        assert "### Network" in md
        assert "DNS resolution failing." in md

    def test_format_empty_report(self) -> None:
        md = format_triage_report(TriageReport())
        assert "No anomalies detected." in md


class TestTriagePriors:
    def test_valid_priors(self) -> None:
        priors = TriagePriors(
            areas=[
                TriageArea(name="Network", hints=["Check DNS", "Check connectivity"]),
                TriageArea(name="Storage", hints=["Check PVCs"]),
            ]
        )
        assert len(priors.areas) == 2
        assert priors.areas[0].name == "Network"

    def test_max_areas_exceeded(self) -> None:
        areas = [TriageArea(name=f"area-{i}", hints=["hint"]) for i in range(13)]
        with pytest.raises(ValueError, match="Maximum 12 triage areas"):
            TriagePriors(areas=areas)

    def test_max_hints_exceeded(self) -> None:
        with pytest.raises(ValueError, match="Maximum 10 hints per area"):
            TriageArea(name="test", hints=[f"hint-{i}" for i in range(11)])

    def test_hint_word_limit_exceeded(self) -> None:
        hint = " ".join(f"word{i}" for i in range(25))
        with pytest.raises(ValueError, match="Each hint must be at most 24 words"):
            TriageArea(name="test", hints=[hint])

    def test_total_hint_words_exceeded(self) -> None:
        hint = " ".join(f"word{i}" for i in range(13))
        hints = [hint for _ in range(10)]
        with pytest.raises(ValueError, match="Maximum 120 total hint words per area"):
            TriageArea(name="test", hints=hints)
