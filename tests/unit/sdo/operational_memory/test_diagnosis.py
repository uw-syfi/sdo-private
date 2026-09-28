"""Contract tests for deterministic verification of a responder's diagnosis."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sdo.contracts import (
    ConfirmedRootCause,
    DetectorEvaluation,
    IncidentRequest,
    IncidentResult,
    ObjectRef,
    ObservedStateChange,
    RepairActionReceipt,
    RootCauseEvidence,
    StateChange,
    StateChanges,
)
from sdo.operational_memory import DiagnosisVerdict, verify_diagnosis

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "contracts"
AFTER = datetime(2026, 7, 9, 18, 12, 30, tzinfo=timezone.utc)
REPAIRED = datetime(2026, 7, 9, 18, 8, tzinfo=timezone.utc)
HEALTH_CLEARED = datetime(2026, 7, 9, 18, 10, tzinfo=timezone.utc)


def _request() -> IncidentRequest:
    return IncidentRequest.model_validate_json(
        (FIXTURE_DIR / "incident_request_state_changes.json").read_text(encoding="utf-8")
    )


def _result(*causes: ConfirmedRootCause, actions: list[RepairActionReceipt] | None = None) -> IncidentResult:
    result = IncidentResult.model_validate_json((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    repairs = [_repair("repair-frontend", ObjectRef(kind="Service", name="frontend"))] if actions is None else actions
    return result.model_copy(update={"confirmed_root_causes": list(causes), "repair_actions": repairs})


def _cause(
    *evidence: RootCauseEvidence,
    explained: tuple[str, ...] = ("missing-configmap",),
    resources: tuple[ObjectRef, ...] = (ObjectRef(kind="Service", name="frontend"),),
    summary: str = "frontend's selector matches no pods",
) -> ConfirmedRootCause:
    return ConfirmedRootCause(
        summary=summary,
        resources=list(resources),
        evidence=list(evidence),
        explained_detectors=list(explained),
    )


def _repair(
    action_id: str,
    *resources: ObjectRef,
    target: str | None = None,
    started_at: datetime = REPAIRED,
    success: bool = True,
) -> RepairActionReceipt:
    return RepairActionReceipt(
        action_id=action_id,
        kind="kubectl",
        target=target or (f"{resources[0].kind}/{resources[0].name}" if resources else "unknown"),
        resources=list(resources),
        summary=f"repair {action_id}",
        details=f"repair {action_id}",
        started_at=started_at,
        completed_at=started_at,
        success=success,
        reversible=False,
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


def _final_state_changes(*changes: StateChange) -> StateChanges:
    return StateChanges(
        baseline_at=datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc),
        observed_at=AFTER,
        changes=list(changes),
    )


def test_a_composites_later_fault_is_confirmed_against_the_closing_views_diff() -> None:
    """N11: K2's readiness-probe fault lands ~6s after the selector fault,

    while the incident is still open, so it is missing from the request's
    dispatch-time diff but present in the controller's diff as of
    verification time.
    """

    cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="geo-config absent"),
        RootCauseEvidence(
            kind="state-change", source="Deployment/frontend", observation="readiness probe path changed"
        ),
    )
    late_fault = StateChange(kind="Deployment", name="frontend", change="modified")
    request = _request()
    assert request.state_changes is not None
    unreverted = list(request.state_changes.changes)

    [verification] = verify_diagnosis(
        request,
        _result(cause, actions=[_repair("fix-probe", ObjectRef(kind="Deployment", name="frontend"))]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_final_state_changes(*unreverted, late_fault),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert [check.verified for check in verification.evidence] == [True, True]
    assert verification.repair is not None
    assert verification.repair.actions == ["fix-probe"]


def test_a_change_absent_from_both_diffs_is_still_contradicted() -> None:
    """The closing view must not turn every citation into a free pass:

    a decoy or a change that never happened stays contradicted even when a
    closing-time diff is available.
    """

    cause = _cause(
        RootCauseEvidence(kind="state-change", source="ConfigMap/failure-admin-geo", observation="decoy script"),
    )
    unrelated_late_fault = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(cause),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_final_state_changes(unrelated_late_fault),
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert [check.verified for check in verification.evidence] == [False]


def test_legacy_results_without_evidence_are_reported_as_such() -> None:
    legacy = ConfirmedRootCause(summary="geo lost its ConfigMap", resources=[ObjectRef(kind="Deployment", name="geo")])

    [verification] = verify_diagnosis(_request(), _result(legacy), final_detector_states=[])

    assert verification.verdict == DiagnosisVerdict.NO_EVIDENCE
    assert verify_diagnosis(_request(), None, final_detector_states=[]) == []


# F8: a confirmed cause must be backed by the responder's own repair.

_NETWORK_POLICY = ObjectRef(kind="NetworkPolicy", namespace="hotel-reservation", name="deny-all")
_CONFIGMAP = ObjectRef(kind="ConfigMap", namespace="hotel-reservation", name="geo-config")
_FRONTEND = ObjectRef(api_version="apps/v1", kind="Deployment", namespace="hotel-reservation", name="frontend")


def _unchanged_except(*reverted: str, extra: tuple[StateChange, ...] = ()) -> StateChanges:
    """The closing diff: the dispatch diff minus the ``Kind/name`` objects restored to their baseline."""

    request = _request()
    assert request.state_changes is not None
    kept = [change for change in request.state_changes.changes if f"{change.kind}/{change.name}" not in reverted]
    return _final_state_changes(*kept, *extra)


def _network_policy_cause() -> ConfirmedRootCause:
    return _cause(
        RootCauseEvidence(kind="state-change", source="NetworkPolicy/deny-all", observation="deny-all added"),
        resources=(_NETWORK_POLICY,),
        summary="NetworkPolicy deny-all isolates frontend",
    )


def _configmap_cause() -> ConfirmedRootCause:
    return _cause(
        RootCauseEvidence(kind="state-change", source="ConfigMap/geo-config", observation="geo-config deleted"),
        resources=(_CONFIGMAP, ObjectRef(kind="Deployment", name="geo")),
        summary="geo lost its required ConfigMap",
    )


def test_a_wrong_cause_is_not_confirmed_when_someone_else_fixed_the_fault() -> None:
    """F8 (``wrong_only_claimed``): the responder blames and restarts frontend.

    Someone else reverts the faulty configuration inside the verification
    window, so every explained detector flips. The restart touched the blamed
    Deployment, but the recovery coincides with configuration changes that the
    responder's repair never reverted.
    """

    wrong = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="requests fail"),
        RootCauseEvidence(kind="live-observation", source="kubectl get pods", observation="frontend wedged"),
        resources=(_FRONTEND,),
        summary="Pods of frontend are wedged after a stale rollout and must be restarted",
    )
    restart_leftover = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(wrong, actions=[_repair("wrong-repair", _FRONTEND, target="frontend")]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(
            "NetworkPolicy/deny-all", "ConfigMap/geo-config", "Service/frontend", extra=(restart_leftover,)
        ),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert all(flip.flipped for flip in verification.detectors)
    assert verification.repair is not None
    assert verification.repair.attributed is False
    assert verification.repair.actions == ["wrong-repair"]
    assert "NetworkPolicy/deny-all" in verification.repair.externally_reverted
    assert "NetworkPolicy/deny-all" in verification.repair.reason


def test_a_cause_whose_resources_no_repair_touched_is_unattributed() -> None:
    [verification] = verify_diagnosis(
        _request(),
        _result(_configmap_cause(), actions=[_repair("restart", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []
    assert "touched none" in verification.repair.reason


def test_a_correct_cause_backed_by_its_own_repair_is_confirmed() -> None:
    [verification] = verify_diagnosis(
        _request(),
        _result(_configmap_cause(), actions=[_repair("restore-configmap", _CONFIGMAP)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.repair is not None
    assert verification.repair.attributed is True
    assert verification.repair.actions == ["restore-configmap"]
    assert verification.repair.repaired_resources == ["ConfigMap/geo-config"]


def test_a_repair_that_started_after_health_cleared_or_failed_does_not_back_the_cause() -> None:
    late = _repair("restore-configmap", _CONFIGMAP, started_at=HEALTH_CLEARED.replace(minute=11))
    failed = _repair("failed-restore", _CONFIGMAP, success=False)

    [verification] = verify_diagnosis(
        _request(),
        _result(_configmap_cause(), actions=[late, failed]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


def test_a_composite_with_both_repairs_confirms_both_causes() -> None:
    actions = [_repair("delete-policy", _NETWORK_POLICY), _repair("restore-configmap", _CONFIGMAP)]

    verifications = verify_diagnosis(
        _request(),
        _result(_network_policy_cause(), _configmap_cause(), actions=actions),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("NetworkPolicy/deny-all", "ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert [verification.verdict for verification in verifications] == [
        DiagnosisVerdict.CONFIRMED,
        DiagnosisVerdict.CONFIRMED,
    ]
    assert [verification.repair.actions for verification in verifications if verification.repair] == [
        ["delete-policy"],
        ["restore-configmap"],
    ]


def test_a_composite_with_one_repair_confirms_only_the_repaired_cause() -> None:
    """Someone else restored geo-config; only the responder's NetworkPolicy repair is its own."""

    verifications = verify_diagnosis(
        _request(),
        _result(_network_policy_cause(), _configmap_cause(), actions=[_repair("delete-policy", _NETWORK_POLICY)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("NetworkPolicy/deny-all", "ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert [verification.verdict for verification in verifications] == [
        DiagnosisVerdict.CONFIRMED,
        DiagnosisVerdict.UNATTRIBUTED,
    ]


def test_a_runtime_cause_outside_the_config_diff_is_confirmed_by_touching_its_workload() -> None:
    """A fault the configuration diff cannot see (a wedged pod) is backed by a repair of its workload."""

    wedged = _cause(
        RootCauseEvidence(kind="live-observation", source="kubectl get pods", observation="geo pod stuck"),
        resources=(ObjectRef(kind="Pod", namespace="hotel-reservation", name="geo-5d8f7c9b6-x2k4q"),),
        summary="a geo pod is wedged",
    )

    [verification] = verify_diagnosis(
        _request(),
        _result(wedged, actions=[_repair("restart-geo", target="deployment.apps/geo")]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.repair is not None
    assert verification.repair.actions == ["restart-geo"]


@pytest.mark.parametrize(
    "target",
    ["ConfigMap/geo-config", "configmap/geo-config", "cm/geo-config", "hotel-reservation/ConfigMap/geo-config"],
)
def test_legacy_free_form_targets_are_parsed_as_kubernetes_objects(target: str) -> None:
    """Receipts without structured resources still attribute through ``Kind/name`` targets."""

    [verification] = verify_diagnosis(
        _request(),
        _result(_configmap_cause(), actions=[_repair("restore", target=target)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except("ConfigMap/geo-config"),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED


# rc2: a state-change citation must predate the responder's own repair.

#: The controller first saw the dispatch diff's changes before any repair.
DISPATCH_OBSERVED = datetime(2026, 7, 9, 18, 4, tzinfo=timezone.utc)


def _observed(*entries: tuple[str, datetime]) -> list[ObservedStateChange]:
    """What the controller saw change while the incident was open: the dispatch diff, then ``entries``."""

    request = _request()
    assert request.state_changes is not None
    seen = [
        ObservedStateChange(kind=change.kind, name=change.name, first_observed_at=DISPATCH_OBSERVED)
        for change in request.state_changes.changes
    ]
    for key, first_observed_at in entries:
        kind, name = key.split("/")
        seen.append(ObservedStateChange(kind=kind, name=name, first_observed_at=first_observed_at))
    return seen


def _own_edit_cause() -> ConfirmedRootCause:
    return _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="requests fail"),
        RootCauseEvidence(kind="state-change", source="Deployment/frontend", observation="frontend rollout changed"),
        resources=(_FRONTEND,),
        summary="a stale frontend rollout broke the service",
    )


def test_a_wrong_fix_citing_its_own_edit_is_not_confirmed() -> None:
    """RC1's open hole: the closing-view diff holds the responder's own restart.

    The real fault is invisible to the configuration diff and recovers by
    other means. The responder restarts frontend and cites the restart's
    ``restartedAt`` change, which the controller first saw only after that
    repair started, as ``state-change`` evidence. Without the observation
    time, this cause was ``confirmed``.
    """

    restart_leftover = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(_own_edit_cause(), actions=[_repair("restart-frontend", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(extra=(restart_leftover,)),
        observed_state_changes=_observed(("Deployment/frontend", REPAIRED.replace(second=5))),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    own_edit = verification.evidence[1]
    assert (own_edit.source, own_edit.verified) == ("Deployment/frontend", False)
    assert own_edit.reason is not None
    assert "restart-frontend" in own_edit.reason


def test_the_same_citation_without_observation_times_was_confirmed() -> None:
    """Records from controllers that predate the observation times keep N11's union rule."""

    restart_leftover = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(_own_edit_cause(), actions=[_repair("restart-frontend", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(extra=(restart_leftover,)),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED


def test_a_late_fault_seen_before_the_repair_is_still_confirmed() -> None:
    """N11 keeps working: K2's late component lands after dispatch but before the responder's repair."""

    late_fault = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(_own_edit_cause(), actions=[_repair("fix-probe", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(extra=(late_fault,)),
        observed_state_changes=_observed(("Deployment/frontend", DISPATCH_OBSERVED.replace(second=6))),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert [check.verified for check in verification.evidence] == [True, True]


def test_a_late_fault_the_responder_reverted_exactly_is_confirmed_from_what_was_observed() -> None:
    """RC1's other direction: an exact revert leaves the late fault in neither diff.

    The controller still saw it change while the incident was open, before the repair.
    """

    [verification] = verify_diagnosis(
        _request(),
        _result(_own_edit_cause(), actions=[_repair("fix-probe", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(),
        observed_state_changes=_observed(("Deployment/frontend", DISPATCH_OBSERVED.replace(second=6))),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED


def test_a_change_first_seen_after_an_unrelated_repair_is_still_evidence() -> None:
    """Only a repair of the cited object makes its change the responder's own."""

    late_fault = StateChange(kind="Deployment", name="frontend", change="modified")

    [verification] = verify_diagnosis(
        _request(),
        _result(
            _own_edit_cause(),
            actions=[
                _repair("restore-configmap", _CONFIGMAP, started_at=REPAIRED.replace(minute=5)),
                _repair("fix-probe", _FRONTEND),
            ],
        ),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(extra=(late_fault,)),
        observed_state_changes=_observed(("Deployment/frontend", REPAIRED.replace(minute=6))),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED


def test_a_change_the_controller_never_observed_is_contradicted() -> None:
    [verification] = verify_diagnosis(
        _request(),
        _result(_own_edit_cause(), actions=[_repair("restart-frontend", _FRONTEND)]),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(),
        observed_state_changes=_observed(),
        health_cleared_at=HEALTH_CLEARED,
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert verification.evidence[1].verified is False
