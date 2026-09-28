"""Contract tests for deterministic verification of a responder's diagnosis."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sdo.contracts import (
    ConfirmedRootCause,
    DetectorEvaluation,
    IncidentRequest,
    IncidentResult,
    ObjectRef,
    RootCauseEvidence,
)
from sdo.operational_memory import DiagnosisVerdict, verify_diagnosis

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "contracts"
AFTER = datetime(2026, 7, 9, 18, 12, 30, tzinfo=timezone.utc)


def _request() -> IncidentRequest:
    return IncidentRequest.model_validate_json(
        (FIXTURE_DIR / "incident_request_state_changes.json").read_text(encoding="utf-8")
    )


def _result(*causes: ConfirmedRootCause) -> IncidentResult:
    result = IncidentResult.model_validate_json((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    return result.model_copy(update={"confirmed_root_causes": list(causes)})


def _cause(*evidence: RootCauseEvidence, explained: tuple[str, ...] = ("missing-configmap",)) -> ConfirmedRootCause:
    return ConfirmedRootCause(
        summary="frontend's selector matches no pods",
        resources=[ObjectRef(kind="Service", name="frontend")],
        evidence=list(evidence),
        explained_detectors=list(explained),
    )


def _clear(detector_id: str) -> DetectorEvaluation:
    return DetectorEvaluation.model_validate(
        {"detector_id": detector_id, "evaluated_at": AFTER.isoformat(), "status": "clear"}
    )


def _firing(detector_id: str) -> DetectorEvaluation:
    return DetectorEvaluation.model_validate(
        {"detector_id": detector_id, "evaluated_at": AFTER.isoformat(), "status": "firing", "fingerprints": ["x"]}
    )


def test_diagnosis_is_confirmed_when_its_evidence_exists_and_its_detectors_flip() -> None:
    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="geo-config absent"),
        RootCauseEvidence(kind="state-change", source="Service/frontend", observation="selector gained a label"),
        RootCauseEvidence(kind="live-observation", source="kubectl get endpoints frontend", observation="<none>"),
    )

    [verification] = verify_diagnosis(_request(), _result(cause), final_detector_states=[_clear("missing-configmap")])

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert [check.verified for check in verification.evidence] == [True, True, None]
    assert verification.detectors[0].fired_at_dispatch
    assert verification.detectors[0].cleared_after_fix is True
    assert verification.detectors[0].flipped


def test_diagnosis_citing_evidence_the_controller_never_saw_is_contradicted() -> None:
    cause = _cause(
        RootCauseEvidence(kind="state-change", source="ConfigMap/failure-admin-geo", observation="decoy script"),
        RootCauseEvidence(kind="synthetic-traffic", source="login", observation="login failed"),
    )

    [verification] = verify_diagnosis(_request(), _result(cause), final_detector_states=[_clear("missing-configmap")])

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert [check.verified for check in verification.evidence] == [False, False]


def test_diagnosis_whose_detectors_did_not_clear_or_never_fired_is_unverified() -> None:
    evidence = RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="geo-config absent")

    still_firing = verify_diagnosis(
        _request(), _result(_cause(evidence)), final_detector_states=[_firing("missing-configmap")]
    )[0]
    never_fired = verify_diagnosis(
        _request(),
        _result(_cause(evidence, explained=("missing-configmap", "traffic-health"))),
        final_detector_states=[_clear("missing-configmap"), _clear("traffic-health")],
    )[0]

    assert still_firing.verdict == DiagnosisVerdict.UNVERIFIED
    assert still_firing.detectors[0].cleared_after_fix is False
    assert never_fired.verdict == DiagnosisVerdict.UNVERIFIED
    assert [flip.fired_at_dispatch for flip in never_fired.detectors] == [True, False]


def test_scenario_evidence_matches_the_traffic_detector_findings() -> None:
    request = _request()
    traffic = request.findings[0].model_copy(
        update={"detector_id": "traffic-health", "rule_id": "scenario-slo.search-hotels"}
    )
    request = request.model_copy(update={"findings": [*request.findings, traffic]})
    cause = _cause(
        RootCauseEvidence(kind="synthetic-traffic", source="search-hotels", observation="connection refused"),
        explained=("traffic-health",),
    )

    [verification] = verify_diagnosis(request, _result(cause), final_detector_states=[_clear("traffic-health")])

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.evidence[0].verified is True


def test_legacy_results_without_evidence_are_reported_as_such() -> None:
    legacy = ConfirmedRootCause(summary="geo lost its ConfigMap", resources=[ObjectRef(kind="Deployment", name="geo")])

    [verification] = verify_diagnosis(_request(), _result(legacy), final_detector_states=[])

    assert verification.verdict == DiagnosisVerdict.NO_EVIDENCE
    assert verify_diagnosis(_request(), None, final_detector_states=[]) == []
