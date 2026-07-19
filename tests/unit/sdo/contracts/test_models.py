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
