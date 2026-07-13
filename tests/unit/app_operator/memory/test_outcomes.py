from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app_operator.memory.models import OutcomeClassification
from app_operator.memory.outcomes import OutcomeFacts, derive_outcome
from app_operator.protocol.models import IncidentRequest, IncidentResult, IncidentStatus

if TYPE_CHECKING:
    from collections.abc import Callable


def _protocol_fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[4]
    return (root / "tests" / "fixtures" / "sdo" / "protocol" / name).read_text(encoding="utf-8")


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
        (lambda facts: facts, OutcomeClassification.SUCCESS),
    ],
)
def test_controller_facts_authoritatively_derive_all_outcome_classes(
    mutate: Callable[[OutcomeFacts], OutcomeFacts],
    expected: OutcomeClassification,
) -> None:
    request = IncidentRequest.model_validate_json(_protocol_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_protocol_fixture("incident_result.json"))
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
