"""The responder degrades a mislabeled detector/scenario citation to a live
observation, so an ad-hoc check (SDO's verify burst, a kubectl read, the
configuration diff) does not contradict an otherwise sound cause, while a
genuine red herring that cites a real finding that never fired still does.
"""

from __future__ import annotations

from pathlib import Path

from sdo.agent_runtime.responder.codex import _normalize_evidence_kinds, _verification_instructions
from sdo.contracts import (
    ConfirmedRootCause,
    DetectorEvaluation,
    Finding,
    IncidentRequest,
    IncidentResult,
    ObjectRef,
    RepairActionReceipt,
    RootCauseEvidence,
)
from sdo.operational_memory import DiagnosisVerdict, verify_diagnosis

FIXTURE_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "sdo" / "contracts"


def _request(*, scenarios: tuple[str, ...] = (), extra_detectors: tuple[str, ...] = ()) -> IncidentRequest:
    request = IncidentRequest.model_validate_json((FIXTURE_DIR / "incident_request.json").read_text(encoding="utf-8"))
    primary = request.findings[0].primary_resource
    findings = [
        *request.findings,
        *(
            Finding(
                detector_id="traffic-health",
                rule_id=f"scenario-slo.{scenario}",
                status="active",
                severity="critical",
                summary=f"{scenario} failing",
                evidence=f"{scenario} returned 503",
                primary_resource=primary,
            )
            for scenario in scenarios
        ),
    ]
    history = [
        *request.detector_history,
        *(
            DetectorEvaluation.model_validate(
                {"detector_id": detector, "evaluated_at": "2026-07-09T18:00:00Z", "status": "clear"}
            )
            for detector in extra_detectors
        ),
    ]
    return request.model_copy(update={"findings": findings, "detector_history": history})


def _result(cause: ConfirmedRootCause) -> IncidentResult:
    result = IncidentResult.model_validate_json((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    repair = RepairActionReceipt(
        action_id="repair-geo",
        kind="kubectl",
        target="ConfigMap/geo-config",
        resources=[ObjectRef(kind="ConfigMap", name="geo-config")],
        summary="recreate geo-config",
        details="recreate geo-config",
        started_at="2026-07-09T18:08:00Z",
        completed_at="2026-07-09T18:08:00Z",
        success=True,
        reversible=False,
    )
    return result.model_copy(update={"confirmed_root_causes": [cause], "repair_actions": [repair]})


def _cause(*evidence: RootCauseEvidence, explained: tuple[str, ...] = ("missing-configmap",)) -> ConfirmedRootCause:
    return ConfirmedRootCause(
        summary="geo-config absent",
        resources=[ObjectRef(kind="ConfigMap", name="geo-config")],
        evidence=list(evidence),
        explained_detectors=list(explained),
    )


def _only(result: IncidentResult) -> list[RootCauseEvidence]:
    (cause,) = result.confirmed_root_causes
    return list(cause.evidence)


def test_detector_finding_with_unknown_source_degrades_to_live_observation() -> None:
    # i09: "change-diff" is the configuration diff, not a detector.
    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="change-diff", observation="geo-config removed"),
    )

    normalized = _normalize_evidence_kinds(_result(cause), _request())

    (item,) = _only(normalized)
    assert item.kind == "live-observation"
    assert item.source == "change-diff"
    assert item.observation == "geo-config removed"


def test_synthetic_traffic_with_unknown_scenario_degrades_to_live_observation() -> None:
    # i03: "sdo-incident-status" is SDO's own verify command, not a scenario.
    cause = _cause(
        RootCauseEvidence(kind="synthetic-traffic", source="sdo-incident-status", observation="exit 1 unhealthy"),
    )

    normalized = _normalize_evidence_kinds(_result(cause), _request())

    (item,) = _only(normalized)
    assert item.kind == "live-observation"
    assert item.source == "sdo-incident-status"


def test_mixed_scenario_source_keeps_known_parts_and_splits_unknown() -> None:
    # i02/i04: three real failing scenarios plus an invented "synthetic-reservation".
    cause = _cause(
        RootCauseEvidence(
            kind="synthetic-traffic",
            source="hotel-search; hotel-recommendations; seeded-user-login; synthetic-reservation",
            observation="all failing with 503",
        ),
    )

    request = _request(scenarios=("hotel-search", "hotel-recommendations", "seeded-user-login"))
    normalized = _normalize_evidence_kinds(_result(cause), request)

    kept, degraded = _only(normalized)
    assert kept.kind == "synthetic-traffic"
    assert kept.source == "hotel-search; hotel-recommendations; seeded-user-login"
    assert degraded.kind == "live-observation"
    assert degraded.source == "synthetic-reservation"
    assert degraded.observation == "all failing with 503"


def test_known_detector_that_did_not_fire_is_not_degraded() -> None:
    # A real detector cited as a finding it never produced is a genuine red
    # herring; the responder leaves it for the verifier to contradict.
    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="orphan-detector", observation="claimed firing"),
    )
    request = _request(extra_detectors=("orphan-detector",))

    normalized = _normalize_evidence_kinds(_result(cause), request)

    (item,) = _only(normalized)
    assert item.kind == "detector-finding"
    assert item.source == "orphan-detector"

    [verification] = verify_diagnosis(request, normalized, final_detector_states=[])
    assert verification.verdict == DiagnosisVerdict.CONTRADICTED


def test_known_evidence_is_left_untouched() -> None:
    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="geo-config absent"),
        RootCauseEvidence(kind="state-change", source="ConfigMap/geo-config", observation="removed"),
        RootCauseEvidence(kind="live-observation", source="kubectl get configmap geo-config", observation="<none>"),
    )
    result = _result(cause)

    normalized = _normalize_evidence_kinds(result, _request())

    assert _only(normalized) == _only(result)


def test_normalized_cause_is_no_longer_contradicted() -> None:
    # End to end: the mislabeled citation stops sinking an otherwise sound cause.
    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="geo-config absent"),
        RootCauseEvidence(
            kind="synthetic-traffic",
            source="hotel-search; synthetic-reservation",
            observation="search failing, reservation flow failing",
        ),
    )
    request = _request(scenarios=("hotel-search",))

    before = verify_diagnosis(request, _result(cause), final_detector_states=[_clear("missing-configmap")])
    assert before[0].verdict == DiagnosisVerdict.CONTRADICTED

    normalized = _normalize_evidence_kinds(_result(cause), request)
    after = verify_diagnosis(request, normalized, final_detector_states=[_clear("missing-configmap")])
    assert after[0].verdict != DiagnosisVerdict.CONTRADICTED
    assert after[0].verdict == DiagnosisVerdict.CONFIRMED


def test_verification_instructions_steer_evidence_kinds() -> None:
    text = _verification_instructions()
    assert "live observation" in text
    # Fault-agnostic steer: ad-hoc checks and the diff are not detector/scenario evidence.
    assert "incident status" in text
    assert "does not recognize" in text


def _clear(detector_id: str) -> DetectorEvaluation:
    return DetectorEvaluation.model_validate(
        {"detector_id": detector_id, "evaluated_at": "2026-07-09T18:12:30Z", "status": "clear"}
    )
