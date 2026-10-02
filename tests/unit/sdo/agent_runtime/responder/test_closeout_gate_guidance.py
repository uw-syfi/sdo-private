"""Responder-side half of the close-out gate: acknowledging diff objects it leaves alone."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from sdo.agent_runtime.responder.codex import CLOSEOUT_STATE_GATE_ENV, _incident_result_schema, _responder_prompt
from sdo.contracts import IncidentRequest, IncidentResult, StateChange, StateChanges

_FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "sdo" / "contracts" / "incident_request.json"


def _request_with_diff() -> IncidentRequest:
    request = IncidentRequest.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))
    now = datetime(2026, 10, 2, tzinfo=UTC)
    changes = StateChanges(
        baseline_at=now, observed_at=now, changes=[StateChange(kind="ConfigMap", name="x", change="modified")]
    )
    return request.model_copy(update={"state_changes": changes})


def test_default_schema_and_prompt_do_not_mention_acknowledgements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLOSEOUT_STATE_GATE_ENV, raising=False)

    assert "acknowledged_state_changes" not in _incident_result_schema()["properties"]
    assert "acknowledged_state_changes" not in _responder_prompt(_request_with_diff())


def test_gate_schema_requires_the_acknowledgement_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLOSEOUT_STATE_GATE_ENV, "1")
    schema = _incident_result_schema()

    assert "acknowledged_state_changes" in schema["required"]
    item = schema["properties"]["acknowledged_state_changes"]["items"]
    assert item["required"] == ["kind", "name", "reason"]
    assert item["additionalProperties"] is False


def test_gate_prompt_names_no_kind_and_asks_for_repair_or_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLOSEOUT_STATE_GATE_ENV, "1")
    prompt = _responder_prompt(_request_with_diff())

    assert "acknowledged_state_changes" in prompt
    assert "sends the incident back" in prompt
    assert "networkpolicy" not in prompt.split("acknowledged_state_changes", 1)[1].lower()


def test_gate_prompt_is_silent_without_a_state_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLOSEOUT_STATE_GATE_ENV, "1")
    request = _request_with_diff().model_copy(update={"state_changes": None})

    assert "acknowledged_state_changes" not in _responder_prompt(request)


def test_result_accepts_an_acknowledgement_and_requires_its_reason() -> None:
    base = {
        "incident_id": "i1",
        "status": "completed",
        "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0},
        "timing": {"started_at": "2026-10-02T00:00:00Z", "completed_at": "2026-10-02T00:00:01Z"},
    }
    ok = IncidentResult.model_validate(
        {**base, "acknowledged_state_changes": [{"kind": "ConfigMap", "name": "x", "reason": "unrelated"}]}
    )
    assert ok.acknowledged_state_changes[0].reason == "unrelated"
    with pytest.raises(ValidationError):
        IncidentResult.model_validate({**base, "acknowledged_state_changes": [{"kind": "ConfigMap", "name": "x"}]})
