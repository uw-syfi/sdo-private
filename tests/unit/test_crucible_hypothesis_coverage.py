"""Unit tests for check_hypothesis_coverage tool and CandidateVerification.unexplained_anomalies."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from pydantic_ai.models.test import TestModel

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.tools import (
    CandidateVerification,
    HypothesisCoverageVerdict,
    SREDeps,
    TriageAnomaly,
    TriageReport,
    check_hypothesis_coverage,
)


def _make_sre_ctx(deps: SREDeps):
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _make_deps(
    tmp_path: Path,
    triage_report: TriageReport | None = None,
    ltm_model_id: TestModel | None = None,
) -> SREDeps:
    shared_file = tmp_path / "session.md"
    shared_file.write_text("")
    return SREDeps(
        namespace="default",
        shared_file=shared_file,  # type: ignore[arg-type]
        iteration=1,
        stage="diagnosis",
        ltm_model_id=ltm_model_id,
        triage_report=triage_report,
    )


def _make_triage_report() -> TriageReport:
    return TriageReport(
        anomalies=[
            TriageAnomaly(
                category="Non-Running Pods",
                resource_kind="Pod",
                resource_name="geo-abc-123",
                namespace="hotel-reservation",
                observation="CrashLoopBackOff, exit code 1",
            ),
            TriageAnomaly(
                category="Services Without Endpoints",
                resource_kind="Service",
                resource_name="geo",
                namespace="hotel-reservation",
                observation="0 ready endpoints",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# CandidateVerification basic tests
# ---------------------------------------------------------------------------


class TestCandidateVerificationBasic:
    def test_confirmed(self) -> None:
        cv = CandidateVerification(
            candidate_index=0,
            root_cause_class="port_conflict",
            root_cause="sidecar port conflict",
            applies=True,
            reasoning="confirmed",
        )
        assert cv.applies is True

    def test_ruled_out(self) -> None:
        cv = CandidateVerification(
            candidate_index=0,
            root_cause_class="port_conflict",
            root_cause="sidecar port conflict",
            applies=False,
            reasoning="ruled out",
        )
        assert cv.applies is False


# ---------------------------------------------------------------------------
# HypothesisCoverageVerdict model
# ---------------------------------------------------------------------------


class TestHypothesisCoverageVerdict:
    def test_accept(self) -> None:
        v = HypothesisCoverageVerdict(
            verdict="accept",
            explained_anomalies=["Pod/geo CrashLoopBackOff", "Service/geo 0 endpoints"],
            unexplained_anomalies=[],
            reasoning="All anomalies explained by sidecar port conflict",
        )
        assert v.verdict == "accept"
        assert len(v.unexplained_anomalies) == 0

    def test_reject(self) -> None:
        v = HypothesisCoverageVerdict(
            verdict="reject",
            explained_anomalies=["Pod/geo CrashLoopBackOff"],
            unexplained_anomalies=["Service/frontend 0 endpoints"],
            reasoning="Hypothesis does not explain frontend service issue",
        )
        assert v.verdict == "reject"
        assert len(v.unexplained_anomalies) == 1


# ---------------------------------------------------------------------------
# check_hypothesis_coverage — error cases
# ---------------------------------------------------------------------------


class TestCheckHypothesisCoverageErrors:
    def test_no_triage_report(self, tmp_path: Path) -> None:
        deps = _make_deps(tmp_path, triage_report=None, ltm_model_id=TestModel())
        ctx = _make_sre_ctx(deps)

        result = asyncio.run(check_hypothesis_coverage(ctx, hypothesis="port conflict in geo"))
        assert "Error" in result
        assert "triage" in result.lower()

    def test_no_model_id(self, tmp_path: Path) -> None:
        deps = _make_deps(tmp_path, triage_report=_make_triage_report(), ltm_model_id=None)
        ctx = _make_sre_ctx(deps)

        result = asyncio.run(check_hypothesis_coverage(ctx, hypothesis="port conflict in geo"))
        assert "Error" in result
        assert "model" in result.lower()


# ---------------------------------------------------------------------------
# check_hypothesis_coverage — subagent is called correctly
# ---------------------------------------------------------------------------


class TestCheckHypothesisCoverageSubagent:
    def test_accept_result(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        verdict = HypothesisCoverageVerdict(
            verdict="accept",
            explained_anomalies=["Pod/geo CrashLoopBackOff", "Service/geo 0 endpoints"],
            unexplained_anomalies=[],
            reasoning="All explained",
        )
        deps = _make_deps(
            tmp_path,
            triage_report=triage,
            ltm_model_id=TestModel(custom_output_args=verdict.model_dump()),
        )
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(check_hypothesis_coverage(ctx, hypothesis="sidecar port conflict in geo pod"))

        parsed = json.loads(result)
        assert parsed["verdict"] == "accept"
        assert len(parsed["unexplained_anomalies"]) == 0

    def test_reject_result(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        verdict = HypothesisCoverageVerdict(
            verdict="reject",
            explained_anomalies=["Pod/geo CrashLoopBackOff"],
            unexplained_anomalies=["Service/geo has 0 endpoints"],
            reasoning="Hypothesis doesn't explain service issue",
        )
        deps = _make_deps(
            tmp_path,
            triage_report=triage,
            ltm_model_id=TestModel(custom_output_args=verdict.model_dump()),
        )
        ctx = _make_sre_ctx(deps)

        with patch.object(PromptRenderer, "render", return_value="rendered prompt"):
            result = asyncio.run(check_hypothesis_coverage(ctx, hypothesis="OOM kill in geo pod"))

        parsed = json.loads(result)
        assert parsed["verdict"] == "reject"
        assert len(parsed["unexplained_anomalies"]) == 1

    def test_subagent_failure_returns_graceful_error(self, tmp_path: Path) -> None:
        triage = _make_triage_report()
        deps = _make_deps(tmp_path, triage_report=triage, ltm_model_id=TestModel())
        ctx = _make_sre_ctx(deps)

        mock_arun = AsyncMock(side_effect=RuntimeError("model unavailable"))

        with (
            patch("sregym_agents.crucible.tools._kb_tools.arun_with_retry", mock_arun),
            patch.object(PromptRenderer, "render", return_value="rendered prompt"),
        ):
            result = asyncio.run(check_hypothesis_coverage(ctx, hypothesis="some hypothesis"))

        assert "failed" in result.lower() or "error" in result.lower()
