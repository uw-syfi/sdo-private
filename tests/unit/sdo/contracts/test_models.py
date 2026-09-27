from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sdo.contracts import IncidentRequest, IncidentResult

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "contracts"


@pytest.mark.parametrize(
    ("fixture_name", "model_type"),
    [
        ("incident_request.json", IncidentRequest),
        ("incident_result.json", IncidentResult),
    ],
)
def test_contract_golden_fixture_round_trip(
    fixture_name: str, model_type: type[IncidentRequest] | type[IncidentResult]
) -> None:
    payload: dict[str, Any] = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))

    model = model_type.model_validate(payload)

    assert model.model_dump(mode="json", exclude_none=True) == payload


def test_contract_models_reject_unknown_fields() -> None:
    payload = json.loads((FIXTURE_DIR / "incident_request.json").read_text(encoding="utf-8"))
    payload["unexpected"] = True

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        IncidentRequest.model_validate(payload)


def test_incident_result_rejects_duplicate_or_non_chronological_repair_actions() -> None:
    payload = json.loads((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    payload["repair_actions"].append(dict(payload["repair_actions"][0]))

    with pytest.raises(ValueError, match="repair action IDs must be unique"):
        IncidentResult.model_validate(payload)

    payload["repair_actions"] = [payload["repair_actions"][0]]
    payload["repair_actions"][0]["completed_at"] = "2026-07-09T17:59:00Z"
    with pytest.raises(ValueError, match="completed_at must not be before started_at"):
        IncidentResult.model_validate(payload)
