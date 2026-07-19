from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from sdo.contracts import IncidentRequest, IncidentResult, IncidentStatus
from sdo.operational_memory.models import OutcomeClassification
from sdo.operational_memory.outcomes import OutcomeFacts, derive_outcome

if TYPE_CHECKING:
    from collections.abc import Callable


def _contract_fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[4]
    return (root / "tests" / "fixtures" / "sdo" / "contracts" / name).read_text(encoding="utf-8")


def _mark_false_negative(facts: OutcomeFacts) -> OutcomeFacts:
    return facts.model_copy(update={"missed_fault_detected": True})


def _mark_failed(facts: OutcomeFacts) -> OutcomeFacts:
    return facts.model_copy(update={"result": None, "dispatch_error": "job failed"})


def _mark_cancelled(facts: OutcomeFacts) -> OutcomeFacts:
    assert facts.result is not None
    cancelled_result = facts.result.model_copy(update={"status": IncidentStatus.CANCELLED})
    return facts.model_copy(update={"result": cancelled_result})


def _mark_partial(facts: OutcomeFacts) -> OutcomeFacts:
    return facts.model_copy(update={"health_verified": False, "verified_at": None})


def _mark_false_positive(facts: OutcomeFacts) -> OutcomeFacts:
    return facts.model_copy(update={"fault_confirmed": False})


def _leave_successful(facts: OutcomeFacts) -> OutcomeFacts:
    return facts


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_mark_false_negative, OutcomeClassification.FALSE_NEGATIVE),
        (_mark_failed, OutcomeClassification.FAILED),
        (_mark_cancelled, OutcomeClassification.CANCELLED),
        (_mark_partial, OutcomeClassification.PARTIAL),
        (_mark_false_positive, OutcomeClassification.FALSE_POSITIVE),
        (_leave_successful, OutcomeClassification.SUCCESS),
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
