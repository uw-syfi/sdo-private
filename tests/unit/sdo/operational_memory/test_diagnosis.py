"""Contract tests for deterministic verification of a responder's diagnosis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sdo.contracts import (
    ConfirmedRootCause,
    DetectorEvaluation,
    DetectorTimelineEntry,
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
from sdo.operational_memory.diagnosis import DetectorFlip

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


# Responder-reported clocks drift: the phase A cold NetworkPolicy run deleted the policy
# at 07:57:07.5, health cleared at 07:57:08.8, yet the receipt's ``started_at`` was read
# at 07:57:17. The controller's own facts (dispatch, responder completion, the diff) are
# the anchor, so a bounded skew is tolerated only inside the responder's session.
_DISPATCHED = datetime(2026, 7, 9, 18, 5, tzinfo=timezone.utc)
_RESPONDER_DONE = datetime(2026, 7, 9, 18, 10, 40, tzinfo=timezone.utc)


def _skewed_policy_repair(skew_seconds: float, *, action_id: str = "delete-policy") -> RepairActionReceipt:
    return _repair(
        action_id,
        _NETWORK_POLICY,
        started_at=datetime.fromtimestamp(HEALTH_CLEARED.timestamp() + skew_seconds, tz=timezone.utc),
    )


def _verify_policy(
    actions: list[RepairActionReceipt],
    *,
    reverted: tuple[str, ...] = ("NetworkPolicy/deny-all",),
    dispatched_at: datetime | None = _DISPATCHED,
    responder_completed_at: datetime | None = _RESPONDER_DONE,
):
    [verification] = verify_diagnosis(
        _request(),
        _result(_network_policy_cause(), actions=actions),
        final_detector_states=[_clear("missing-configmap")],
        final_state_changes=_unchanged_except(*reverted),
        health_cleared_at=HEALTH_CLEARED,
        dispatched_at=dispatched_at,
        responder_completed_at=responder_completed_at,
    )
    return verification


def test_a_repair_whose_reported_start_is_a_few_seconds_after_health_cleared_is_attributed_inside_the_session() -> None:
    """The exact phase A timeline: the policy is gone from the closing diff and the action lists it."""

    verification = _verify_policy([_skewed_policy_repair(8.5)])

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.repair is not None
    assert verification.repair.attributed is True
    assert verification.repair.actions == ["delete-policy"]
    assert verification.repair.clock_skew_corrected == ["delete-policy"]
    assert verification.repair.externally_reverted == []


def test_an_on_time_repair_is_not_marked_as_skew_corrected() -> None:
    verification = _verify_policy([_skewed_policy_repair(-2.0)])

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.repair is not None
    assert verification.repair.clock_skew_corrected == []


def test_a_skew_beyond_the_tolerance_does_not_back_the_cause() -> None:
    verification = _verify_policy([_skewed_policy_repair(90.0)])

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []
    assert verification.repair.clock_skew_corrected == []


def test_a_skewed_repair_reported_after_the_responder_session_ended_is_not_credited() -> None:
    # Within the skew bound of health_cleared_at, but the responder had already completed.
    early_done = datetime(2026, 7, 9, 18, 8, tzinfo=timezone.utc)
    verification = _verify_policy([_skewed_policy_repair(8.5)], responder_completed_at=early_done)

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


def test_a_skewed_repair_is_not_credited_without_the_session_window() -> None:
    verification = _verify_policy([_skewed_policy_repair(8.5)], dispatched_at=None, responder_completed_at=None)

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


def test_a_skewed_repair_does_not_back_a_fault_that_is_still_present() -> None:
    """The closing diff still shows the policy: the action did not restore it, whatever its clock says."""

    verification = _verify_policy([_skewed_policy_repair(8.5)], reverted=())

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


def test_a_skewed_repair_of_an_object_outside_the_dispatch_diff_is_not_credited() -> None:
    """A revert that the controller never saw as a dispatch-time change cannot be pinned on the action."""

    unrelated = ObjectRef(kind="NetworkPolicy", namespace="hotel-reservation", name="other-policy")
    skewed = _repair(
        "delete-other",
        unrelated,
        started_at=datetime.fromtimestamp(HEALTH_CLEARED.timestamp() + 8.5, tz=timezone.utc),
    )

    verification = _verify_policy([skewed])

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


def test_an_external_revert_stays_unattributed_when_the_responder_touched_something_else() -> None:
    """F8 still holds: the policy was reverted by someone else while the responder restarted frontend."""

    skewed_restart = _repair(
        "restart-frontend",
        _FRONTEND,
        started_at=datetime.fromtimestamp(HEALTH_CLEARED.timestamp() + 8.5, tz=timezone.utc),
    )

    verification = _verify_policy([skewed_restart])

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []
    assert verification.repair.clock_skew_corrected == []
    assert "NetworkPolicy/deny-all" in verification.repair.externally_reverted


def test_a_failed_skewed_repair_is_not_credited() -> None:
    failed = _repair(
        "delete-policy",
        _NETWORK_POLICY,
        started_at=datetime.fromtimestamp(HEALTH_CLEARED.timestamp() + 8.5, tz=timezone.utc),
        success=False,
    )

    verification = _verify_policy([failed])

    assert verification.verdict == DiagnosisVerdict.UNATTRIBUTED
    assert verification.repair is not None
    assert verification.repair.actions == []


# Late findings: in a composite the faults land a few seconds apart, so a learned detector can
# activate after dispatch and the responder cites it through pull-before-act. The mixed mini
# stream (phase A step 3) marked 12 such causes ``contradicted`` because the verifier only
# counted the dispatch snapshot. The controller's detector timeline is the independent record.
_LATE_AT = _DISPATCHED + timedelta(seconds=7)
_LATE_CLEARED = datetime(2026, 7, 9, 18, 9, tzinfo=timezone.utc)


def _timeline_entry(
    detector_id: str,
    *,
    rule_id: str = "rule",
    relation: str = "after_dispatch",
    activated_at: datetime = _LATE_AT,
    cleared_at: datetime | None = _LATE_CLEARED,
) -> DetectorTimelineEntry:
    return DetectorTimelineEntry.model_validate(
        {
            "detector_id": detector_id,
            "rule_id": rule_id,
            "fingerprint": f"{detector_id}/{rule_id}/ns/obj",
            "first_activated_at": activated_at.isoformat(),
            "last_seen_at": (cleared_at or activated_at).isoformat(),
            "cleared_at": None if cleared_at is None else cleared_at.isoformat(),
            "relation": relation,
        }
    )


def _late_cause(*sources: str, explained: tuple[str, ...] = ("late-configmap-detector",)) -> ConfirmedRootCause:
    return _cause(
        *(RootCauseEvidence(kind="detector-finding", source=source, observation="reported") for source in sources),
        explained=explained,
        resources=(_CONFIGMAP, ObjectRef(kind="Deployment", name="geo")),
        summary="geo lost its required ConfigMap",
    )


def _verify_late(
    cause: ConfirmedRootCause,
    timeline: list[DetectorTimelineEntry],
    *,
    final_states: list[DetectorEvaluation] | None = None,
    health_cleared_at: datetime | None = HEALTH_CLEARED,
    responder_completed_at: datetime | None = _RESPONDER_DONE,
    request: IncidentRequest | None = None,
):
    [verification] = verify_diagnosis(
        request or _request(),
        _result(cause, actions=[_repair("restore-configmap", _CONFIGMAP)]),
        final_detector_states=[] if final_states is None else final_states,
        final_state_changes=_unchanged_except("ConfigMap/geo-config"),
        health_cleared_at=health_cleared_at,
        dispatched_at=_DISPATCHED,
        responder_completed_at=responder_completed_at,
        detector_timeline=timeline,
    )
    return verification


def test_a_detector_finding_that_activated_after_dispatch_confirms_the_cause() -> None:
    verification = _verify_late(_late_cause("late-configmap-detector"), [_timeline_entry("late-configmap-detector")])

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert [check.verified for check in verification.evidence] == [True]
    [flip] = verification.detectors
    assert (flip.fired_at_dispatch, flip.fired_after_dispatch, flip.cleared_after_fix, flip.flipped) == (
        False,
        True,
        True,
        True,
    )


def test_a_dispatch_time_finding_is_not_marked_as_firing_after_dispatch() -> None:
    [verification] = verify_diagnosis(
        _request(),
        _result(_cause(RootCauseEvidence(kind="detector-finding", source="missing-configmap", observation="absent"))),
        final_detector_states=[_clear("missing-configmap")],
    )

    [flip] = verification.detectors
    assert (flip.fired_at_dispatch, flip.fired_after_dispatch, flip.flipped) == (True, False, True)


def test_a_detector_that_never_fired_during_the_incident_is_still_contradicted() -> None:
    verification = _verify_late(
        _late_cause("late-configmap-detector"), [_timeline_entry("some-other-detector")], final_states=[]
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert [check.verified for check in verification.evidence] == [False]
    assert "late-configmap-detector" in (verification.evidence[0].reason or "")


def test_a_finding_that_never_joined_an_incident_is_not_evidence() -> None:
    entry = _timeline_entry("late-configmap-detector", relation="no_incident")

    assert _verify_late(_late_cause("late-configmap-detector"), [entry]).verdict == DiagnosisVerdict.CONTRADICTED


def test_a_finding_that_activated_after_health_cleared_does_not_confirm_a_cause() -> None:
    entry = _timeline_entry(
        "late-configmap-detector",
        activated_at=HEALTH_CLEARED + timedelta(seconds=5),
        cleared_at=HEALTH_CLEARED + timedelta(seconds=30),
    )

    verification = _verify_late(_late_cause("late-configmap-detector"), [entry])

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert [flip.fired_after_dispatch for flip in verification.detectors] == [False]


def test_a_finding_that_activated_after_the_responder_finished_does_not_confirm_a_cause() -> None:
    done = _DISPATCHED + timedelta(minutes=2)
    entry = _timeline_entry(
        "late-configmap-detector", activated_at=done + timedelta(seconds=1), cleared_at=done + timedelta(seconds=9)
    )

    verification = _verify_late(
        _late_cause("late-configmap-detector"), [entry], health_cleared_at=None, responder_completed_at=done
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED


def test_a_late_detector_that_never_cleared_leaves_the_cause_unverified() -> None:
    entry = _timeline_entry("late-configmap-detector", cleared_at=None)

    verification = _verify_late(_late_cause("late-configmap-detector"), [entry])

    assert verification.verdict == DiagnosisVerdict.UNVERIFIED
    assert [check.verified for check in verification.evidence] == [True]
    assert [flip.cleared_after_fix for flip in verification.detectors] == [None]


def test_a_post_response_evaluation_outranks_the_timeline_clear() -> None:
    entry = _timeline_entry("late-configmap-detector")

    verification = _verify_late(
        _late_cause("late-configmap-detector"), [entry], final_states=[_firing("late-configmap-detector")]
    )

    assert verification.verdict == DiagnosisVerdict.UNVERIFIED
    assert [flip.cleared_after_fix for flip in verification.detectors] == [False]


def test_without_a_timeline_the_dispatch_only_rule_still_holds() -> None:
    [verification] = verify_diagnosis(
        _request(),
        _result(_late_cause("late-configmap-detector")),
        final_detector_states=[_clear("late-configmap-detector")],
    )

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED


def test_joined_detector_sources_are_checked_one_by_one() -> None:
    entry = _timeline_entry("late-configmap-detector")
    joined = "late-configmap-detector; missing-configmap"

    verification = _verify_late(
        _late_cause(joined, explained=("late-configmap-detector", "missing-configmap")),
        [entry],
        final_states=[_clear("missing-configmap")],
    )

    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert [check.verified for check in verification.evidence] == [True]


def test_one_unfired_part_of_a_joined_source_contradicts_the_citation_and_names_it() -> None:
    entry = _timeline_entry("late-configmap-detector")
    joined = "late-configmap-detector, ghost-detector"

    verification = _verify_late(_late_cause(joined), [entry])

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    assert verification.evidence[0].verified is False
    assert "ghost-detector" in (verification.evidence[0].reason or "")
    assert "late-configmap-detector" not in (verification.evidence[0].reason or "")


def _traffic_request(*scenarios: str) -> IncidentRequest:
    request = _request()
    findings = [
        request.findings[0].model_copy(update={"detector_id": "traffic-health", "rule_id": f"scenario-slo.{name}"})
        for name in scenarios
    ]
    return request.model_copy(update={"findings": [*request.findings, *findings]})


def test_joined_synthetic_traffic_scenarios_are_matched_individually() -> None:
    cause = _cause(
        RootCauseEvidence(kind="synthetic-traffic", source="hotel-login, hotel-search", observation="failing"),
        explained=("traffic-health",),
    )

    [verification] = verify_diagnosis(
        _traffic_request("hotel-login", "hotel-search"),
        _result(cause),
        final_detector_states=[_clear("traffic-health")],
    )

    assert verification.evidence[0].verified is True


def test_a_scenario_that_activated_after_dispatch_is_matched_from_the_timeline() -> None:
    cause = _late_cause("traffic-health", explained=("traffic-health",))
    cause = cause.model_copy(
        update={
            "evidence": [
                RootCauseEvidence(kind="synthetic-traffic", source="hotel-login, hotel-search", observation="failing")
            ]
        }
    )
    timeline = [_timeline_entry("traffic-health", rule_id="scenario-slo.hotel-search")]

    verification = _verify_late(cause, timeline, request=_traffic_request("hotel-login"))

    assert verification.evidence[0].verified is True
    assert verification.verdict == DiagnosisVerdict.CONFIRMED


def test_scenarios_no_finding_ever_reported_stay_contradicted_with_the_parts_named() -> None:
    """Phase A mini stream b, incident 3: the readiness cause cited three scenarios no finding named."""

    cause = _cause(
        RootCauseEvidence(
            kind="synthetic-traffic", source="hotel-login, hotel-search, hotel-recommendations", observation="slow"
        ),
        explained=("traffic-health",),
    )

    [verification] = verify_diagnosis(_request(), _result(cause), final_detector_states=[_clear("traffic-health")])

    assert verification.verdict == DiagnosisVerdict.CONTRADICTED
    reason = verification.evidence[0].reason or ""
    assert all(name in reason for name in ("hotel-login", "hotel-search", "hotel-recommendations"))


def test_old_flip_records_without_the_after_dispatch_field_still_validate() -> None:
    flip = DetectorFlip.model_validate(
        {"detector_id": "missing-configmap", "fired_at_dispatch": True, "cleared_after_fix": True, "flipped": True}
    )

    assert flip.fired_after_dispatch is False


# Replay of phase A mini stream a, incident 4 (composite3c: ConfigMap + NetworkPolicy + readiness):
# the learned detectors activated 7 s, 14 s and 20 s after dispatch, the responder pulled and cited
# them, and the verifier marked both causes contradicted. Times are the receipt's own.
_A4_DISPATCH = datetime(2026, 10, 2, 9, 17, 10, 850000, tzinfo=timezone.utc)
_A4_HEALTH_CLEARED = datetime(2026, 10, 2, 9, 18, 31, 700000, tzinfo=timezone.utc)
_A4_DONE = datetime(2026, 10, 2, 9, 18, 50, tzinfo=timezone.utc)


def _a4(detector_id: str, rule_id: str, activated: str, cleared: str) -> DetectorTimelineEntry:
    day = "2026-10-02T09:"
    return _timeline_entry(
        detector_id,
        rule_id=rule_id,
        activated_at=datetime.fromisoformat(f"{day}{activated}+00:00"),
        cleared_at=datetime.fromisoformat(f"{day}{cleared}+00:00"),
    )


def test_replay_of_the_mixed_mini_stream_a4_composite_confirms_the_late_cited_causes() -> None:
    request = _request().model_copy(
        update={
            "findings": [
                _request().findings[0].model_copy(update={"detector_id": "health-objective", "rule_id": "deployment"}),
                _request().findings[0].model_copy(update={"detector_id": "service-endpoints", "rule_id": "endpoints"}),
            ],
            "detector_history": [_firing("health-objective"), _firing("service-endpoints")],
        }
    )
    timeline = [
        _a4("missing-deployment-configmap", "required-configmap-absent", "17:17.585035", "18:06.758250"),
        _a4("deny-all-network-policy", "selected-pods-denied-all", "17:24.781060", "19:05.448715"),
        _a4("traffic-links", "link-reachability.frontend.user.8086", "17:31.094149", "18:37.094634"),
    ]
    configmap_cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="missing-deployment-configmap", observation="late finding"),
        RootCauseEvidence(kind="live-observation", source="kubectl get configmap mongo-geo-script", observation="x"),
        explained=("missing-deployment-configmap", "health-objective", "service-endpoints"),
        resources=(_CONFIGMAP,),
        summary="mongo-geo-script is missing",
    )
    policy_cause = _cause(
        RootCauseEvidence(kind="detector-finding", source="deny-all-network-policy", observation="late finding"),
        RootCauseEvidence(kind="detector-finding", source="traffic-links", observation="late finding"),
        explained=("deny-all-network-policy", "traffic-links"),
        resources=(_NETWORK_POLICY,),
        summary="deny-all isolates user",
    )
    result = _result(
        configmap_cause, policy_cause, actions=[_repair("restore", _CONFIGMAP), _repair("drop", _NETWORK_POLICY)]
    )

    verifications = verify_diagnosis(
        request,
        result,
        final_detector_states=[_clear("health-objective"), _clear("service-endpoints"), _clear("traffic-links")],
        health_cleared_at=_A4_HEALTH_CLEARED,
        dispatched_at=_A4_DISPATCH,
        responder_completed_at=_A4_DONE,
        detector_timeline=timeline,
    )

    for verification in verifications:
        assert all(check.verified is not False for check in verification.evidence)
        assert all(flip.flipped for flip in verification.detectors)
        assert verification.verdict not in (DiagnosisVerdict.CONTRADICTED, DiagnosisVerdict.UNVERIFIED)
