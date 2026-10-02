from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from sdo.contracts import (
    ConfirmedRootCause,
    IncidentRequest,
    IncidentResult,
    IncidentStatus,
    ObjectRef,
    ObservedStateChange,
    RootCauseEvidence,
    StateChange,
    StateChanges,
)
from sdo.operational_memory import DiagnosisVerdict
from sdo.operational_memory.models import OutcomeClassification
from sdo.operational_memory.outcomes import OutcomeFacts, derive_outcome

if TYPE_CHECKING:
    from collections.abc import Callable


def _contract_fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[4]
    return (root / "tests" / "fixtures" / "sdo" / "contracts" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda facts: facts.model_copy(update={"missed_fault_detected": True}), OutcomeClassification.FALSE_NEGATIVE),
        (
            lambda facts: facts.model_copy(update={"result": None, "dispatch_error": "job failed"}),
            OutcomeClassification.FAILED,
        ),
        (
            lambda facts: facts.model_copy(
                update={"result": facts.result.model_copy(update={"status": IncidentStatus.CANCELLED})}
            ),
            OutcomeClassification.CANCELLED,
        ),
        (
            lambda facts: facts.model_copy(update={"health_verified": False, "verified_at": None}),
            OutcomeClassification.PARTIAL,
        ),
        (lambda facts: facts.model_copy(update={"fault_confirmed": False}), OutcomeClassification.FALSE_POSITIVE),
        (
            # A healed stray: completed, health already clear, no successful repair action and
            # no repository commit. This must not be credited as SUCCESS/FALSE_POSITIVE or
            # learned from (CHAOS_DECISIONS.md F16).
            lambda facts: facts.model_copy(update={"result": facts.result.model_copy(update={"repair_actions": []})}),
            OutcomeClassification.CLEARED_WITHOUT_ACTION,
        ),
        (
            # The same no-op reported as cancelled (phase-1 S3 flicker stages): health cleared with
            # no mutation by the responder, so it is not a mitigation and not merely cancelled (D30).
            lambda facts: facts.model_copy(
                update={
                    "result": facts.result.model_copy(update={"status": IncidentStatus.CANCELLED, "repair_actions": []})
                }
            ),
            OutcomeClassification.CLEARED_WITHOUT_ACTION,
        ),
        (
            # A cancelled responder that never restored health is still just cancelled.
            lambda facts: facts.model_copy(
                update={
                    "health_verified": False,
                    "verified_at": None,
                    "result": facts.result.model_copy(
                        update={"status": IncidentStatus.CANCELLED, "repair_actions": []}
                    ),
                }
            ),
            OutcomeClassification.CANCELLED,
        ),
        (lambda facts: facts, OutcomeClassification.SUCCESS),
    ],
)
def test_controller_facts_authoritatively_derive_all_outcome_classes(
    mutate: Callable[[OutcomeFacts], OutcomeFacts],
    expected: OutcomeClassification,
) -> None:
    request = IncidentRequest.model_validate_json(_contract_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_contract_fixture("incident_result.json"))
    detected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    facts = OutcomeFacts(
        request=request,
        result=result,
        final_health_detector_state=result.final_detector_states,
        health_verified=True,
        fault_confirmed=True,
        inspected_playbooks=[playbook.path for playbook in request.surfaced_playbooks],
        confirmed_playbooks=[playbook.path for playbook in result.applied_playbooks],
        responder_backend="codex",
        responder_model="gpt-5",
        detected_at=detected,
        dispatched_at=detected + timedelta(seconds=1),
        responder_completed_at=detected + timedelta(seconds=2),
        verified_at=detected + timedelta(seconds=3),
    )

    mutated = mutate(facts)
    outcome = derive_outcome(mutated)

    assert outcome.classification == expected
    assert outcome.incident_id == request.incident_id
    assert outcome.surfaced_playbooks == [playbook.path for playbook in request.surfaced_playbooks]
    expected_applied = (
        [] if mutated.result is None else [playbook.path for playbook in mutated.result.applied_playbooks]
    )
    assert outcome.applied_playbooks == expected_applied
    assert outcome.final_health_detector_state == result.final_detector_states


def test_outcome_records_the_diagnosis_verification() -> None:
    request = IncidentRequest.model_validate_json(_contract_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_contract_fixture("incident_result.json"))
    detected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    facts = OutcomeFacts(
        request=request,
        result=result,
        final_health_detector_state=result.final_detector_states,
        health_verified=True,
        fault_confirmed=True,
        responder_backend="codex",
        responder_model="gpt-5",
        detected_at=detected,
        dispatched_at=detected + timedelta(seconds=1),
        responder_completed_at=detected + timedelta(seconds=2),
        verified_at=detected + timedelta(seconds=3),
    )

    outcome = derive_outcome(facts)

    [verification] = outcome.diagnosis_verification
    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.detectors[0].detector_id == "missing-configmap"
    assert verification.detectors[0].flipped


def test_outcome_verifies_state_change_evidence_against_the_controllers_closing_diff() -> None:
    """N11: OutcomeFacts.final_state_changes is the controller's diff as of

    verification time. A composite's later fault can land after the
    request's dispatch-time diff was taken but is present here; derive_outcome
    must thread it into diagnosis verification so citing that fault is
    confirmed rather than contradicted.
    """

    request = IncidentRequest.model_validate_json(_contract_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_contract_fixture("incident_result.json"))
    late_cause = ConfirmedRootCause(
        summary="frontend's readiness probe also changed",
        resources=result.confirmed_root_causes[0].resources,
        evidence=[
            RootCauseEvidence(
                kind="state-change", source="Deployment/frontend", observation="readiness probe path changed"
            )
        ],
        explained_detectors=[],
    )
    result = result.model_copy(update={"confirmed_root_causes": [*result.confirmed_root_causes, late_cause]})
    detected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    facts = OutcomeFacts(
        request=request,
        result=result,
        final_health_detector_state=result.final_detector_states,
        final_state_changes=StateChanges(
            baseline_at=detected,
            observed_at=detected + timedelta(seconds=6),
            changes=[StateChange(kind="Deployment", name="frontend", change="modified")],
        ),
        health_verified=True,
        fault_confirmed=True,
        responder_backend="codex",
        responder_model="gpt-5",
        detected_at=detected,
        dispatched_at=detected + timedelta(seconds=1),
        responder_completed_at=detected + timedelta(seconds=2),
        verified_at=detected + timedelta(seconds=3),
    )

    outcome = derive_outcome(facts)

    late_verification = outcome.diagnosis_verification[1]
    assert late_verification.evidence[0].verified is True


def _attribution_facts(*causes: ConfirmedRootCause, repaired: tuple[str, ...]) -> OutcomeFacts:
    """A verified closure over the state-change fixture in which every dispatch change was reverted.

    ``repaired`` names the ``Kind/name`` objects the responder's own actions touched.
    """

    request = IncidentRequest.model_validate_json(_contract_fixture("incident_request_state_changes.json"))
    result = IncidentResult.model_validate_json(_contract_fixture("incident_result.json"))
    template = result.repair_actions[0]
    actions = [
        template.model_copy(
            update={
                "action_id": f"repair-{index}",
                "target": key,
                "resources": [ObjectRef(kind=key.split("/")[0], name=key.split("/")[1])],
            }
        )
        for index, key in enumerate(repaired)
    ]
    result = result.model_copy(update={"confirmed_root_causes": list(causes), "repair_actions": actions})
    cleared = template.started_at + timedelta(minutes=5)
    return OutcomeFacts(
        request=request,
        result=result,
        final_health_detector_state=result.final_detector_states,
        final_state_changes=StateChanges(baseline_at=cleared, observed_at=cleared, changes=[]),
        health_cleared_at=cleared,
        health_verified=True,
        fault_confirmed=True,
        responder_backend="codex",
        responder_model="gpt-5",
        detected_at=template.started_at - timedelta(minutes=2),
        dispatched_at=template.started_at - timedelta(minutes=1),
        responder_completed_at=cleared,
        verified_at=cleared + timedelta(seconds=30),
    )


def _blaming(kind: str, name: str) -> ConfirmedRootCause:
    return ConfirmedRootCause(
        summary=f"{kind} {name} changed",
        resources=[ObjectRef(kind=kind, name=name)],
        evidence=[RootCauseEvidence(kind="state-change", source=f"{kind}/{name}", observation="changed")],
        explained_detectors=["missing-configmap"],
    )


def test_health_restored_by_someone_else_is_an_external_recovery_not_a_success() -> None:
    """F8: every cited cause is unattributed, so SDO did not recover the incident."""

    wrong = ConfirmedRootCause(
        summary="frontend pods are wedged",
        resources=[ObjectRef(kind="Deployment", name="frontend")],
        evidence=[RootCauseEvidence(kind="live-observation", source="kubectl get pods", observation="wedged")],
        explained_detectors=["missing-configmap"],
    )

    outcome = derive_outcome(_attribution_facts(wrong, repaired=("Deployment/frontend",)))

    assert outcome.classification == OutcomeClassification.EXTERNAL_RECOVERY
    assert [verification.verdict for verification in outcome.diagnosis_verification] == [DiagnosisVerdict.UNATTRIBUTED]
    # Health really was verified in time; only the credit is withheld.
    assert outcome.timestamps.verified_at is not None


def test_a_composite_with_one_own_repair_is_still_an_sdo_success() -> None:
    outcome = derive_outcome(
        _attribution_facts(
            _blaming("NetworkPolicy", "deny-all"),
            _blaming("ConfigMap", "geo-config"),
            repaired=("NetworkPolicy/deny-all",),
        )
    )

    assert outcome.classification == OutcomeClassification.SUCCESS
    assert [verification.verdict for verification in outcome.diagnosis_verification] == [
        DiagnosisVerdict.CONFIRMED,
        DiagnosisVerdict.UNATTRIBUTED,
    ]


def test_a_cause_citing_the_responders_own_edit_is_contradicted_in_the_outcome() -> None:
    """rc2: the observation times reach verification through the outcome facts."""

    own_edit = ConfirmedRootCause(
        summary="a stale frontend rollout broke the service",
        resources=[ObjectRef(kind="Deployment", name="frontend")],
        evidence=[RootCauseEvidence(kind="state-change", source="Deployment/frontend", observation="rollout")],
        explained_detectors=["missing-configmap"],
    )
    facts = _attribution_facts(own_edit, repaired=("Deployment/frontend",))
    assert facts.result is not None
    repaired_at = facts.result.repair_actions[0].started_at
    facts = facts.model_copy(
        update={
            "final_state_changes": StateChanges(
                baseline_at=repaired_at,
                observed_at=repaired_at,
                changes=[StateChange(kind="Deployment", name="frontend", change="modified")],
            ),
            "observed_state_changes": [
                ObservedStateChange(
                    kind="Deployment", name="frontend", first_observed_at=repaired_at + timedelta(seconds=3)
                )
            ],
        }
    )

    outcome = derive_outcome(facts)

    assert [verification.verdict for verification in outcome.diagnosis_verification] == [DiagnosisVerdict.CONTRADICTED]


def test_a_repair_with_a_slightly_late_reported_clock_is_still_an_sdo_success_and_learnable() -> None:
    """The phase A cold NetworkPolicy run: the delete preceded health clearing, the receipt's clock did not.

    The controller's dispatch/completion times and the closing diff anchor the bounded skew
    correction, so the outcome is SUCCESS (reflection may learn) instead of EXTERNAL_RECOVERY.
    """

    facts = _attribution_facts(_blaming("NetworkPolicy", "deny-all"), repaired=("NetworkPolicy/deny-all",))
    assert facts.result is not None
    assert facts.health_cleared_at is not None
    late = facts.result.repair_actions[0].model_copy(
        update={
            "started_at": facts.health_cleared_at + timedelta(seconds=8, milliseconds=500),
            "completed_at": facts.health_cleared_at + timedelta(seconds=9),
        }
    )
    facts = facts.model_copy(
        update={
            "result": facts.result.model_copy(update={"repair_actions": [late]}),
            "responder_completed_at": facts.health_cleared_at + timedelta(seconds=20),
            "verified_at": facts.health_cleared_at + timedelta(seconds=30),
        }
    )

    outcome = derive_outcome(facts)

    assert outcome.classification == OutcomeClassification.SUCCESS
    [verification] = outcome.diagnosis_verification
    assert verification.verdict == DiagnosisVerdict.CONFIRMED
    assert verification.repair is not None
    assert verification.repair.clock_skew_corrected == ["repair-0"]
