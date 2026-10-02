"""A repair is credited only if it started before health cleared, so its receipt times must be measured."""

from __future__ import annotations

from pathlib import Path

from sdo.agent_runtime.responder.codex import _responder_prompt
from sdo.contracts import IncidentRequest

_FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "sdo" / "contracts" / "incident_request.json"


def test_prompt_asks_for_measured_repair_times_not_estimates() -> None:
    request = IncidentRequest.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))

    prompt = _responder_prompt(request)

    assert "date -u" in prompt
    assert "never estimate" in prompt
    assert "started_at" in prompt
