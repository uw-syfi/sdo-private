from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sdo.operational_memory.broker_service import BrokerClosure

_NOW = "2026-09-30T10:00:00Z"


def _closure_payload(**extra: Any) -> dict[str, Any]:
    finding = {
        "detector_id": "cause",
        "rule_id": "cause",
        "status": "active",
        "severity": "warn",
        "summary": "fault",
        "evidence": "evidence",
        "primary_resource": {"kind": "Deployment", "name": "web"},
        "fingerprint": "cause",
    }
    payload: dict[str, Any] = {
        "request": {
            "application": "demo",
            "namespace": "demo",
            "incident_id": "demo-1",
            "findings": [finding],
            "detector_history": [{"detector_id": "cause", "evaluated_at": _NOW, "status": "firing"}],
            "source_commit": "s",
            "deployed_commit": "d",
            "architecture_summary_path": ".sdo/arch.md",
            "health_objective_path": ".sdo/goal.md",
            "repository_worktree": "/repo",
            "repository_base_commit": "b",
            "response_deadline": _NOW,
            "cancellation_token": "cancel",
        },
        "detected_at": _NOW,
        "dispatched_at": _NOW,
        "responder_completed_at": _NOW,
        "verified_at": _NOW,
    }
    payload.update(extra)
    return payload


def _entry(**extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "detector_id": "cause",
        "detector_class": "incident",
        "owner": "responder",
        "rule_id": "cause",
        "fingerprint": "cause",
        "parameter_bindings": {"deployment": {"kind": "Deployment", "name": "web"}},
        "surfaced_playbooks": [".sdo/playbooks/x/README.md"],
        "first_activated_at": "2026-09-30T10:00:01Z",
        "last_seen_at": "2026-09-30T10:00:05Z",
        "cleared_at": "2026-09-30T10:00:09Z",
        "relation": "before_dispatch",
        "activations": 1,
    }
    entry.update(extra)
    return entry


def test_closure_without_firing_telemetry_still_validates() -> None:
    closure = BrokerClosure.model_validate(_closure_payload())

    assert closure.detector_timeline == []
    assert not closure.incident_detector_fired_before_dispatch
    assert not closure.incident_detector_fired_after_dispatch
    assert not closure.no_incident_detector_fired


def test_closure_carries_the_detector_timeline_and_derived_booleans() -> None:
    closure = BrokerClosure.model_validate(
        _closure_payload(
            detector_timeline=[_entry()],
            incident_detector_fired_before_dispatch=True,
            incident_detector_fired_after_dispatch=False,
            no_incident_detector_fired=False,
        )
    )

    (entry,) = closure.detector_timeline
    assert entry.relation == "before_dispatch"
    assert entry.parameter_bindings["deployment"].name == "web"
    assert closure.incident_detector_fired_before_dispatch
    assert BrokerClosure.model_validate(closure.model_dump(mode="json")) == closure


@pytest.mark.parametrize(
    "bad",
    [
        {"relation": "during_dispatch"},
        {"cleared_at": "2026-09-30T09:00:00Z"},
        {"last_seen_at": "2026-09-30T09:00:00Z"},
        {"unexpected": 1},
    ],
)
def test_timeline_entries_reject_malformed_records(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BrokerClosure.model_validate(_closure_payload(detector_timeline=[_entry(**bad)]))
