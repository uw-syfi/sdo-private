from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sdo.contracts import IncidentRequest, IncidentResult, UsageMetrics

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


class TestUsageMetrics:
    def test_a_normalized_breakdown_validates(self) -> None:
        usage = UsageMetrics(
            llm_calls=1,
            input_tokens=1_000,
            output_tokens=80,
            cached_input_tokens=600,
            cache_read_input_tokens=600,
            cache_write_input_tokens=300,
            cache_write_1h_input_tokens=100,
            uncached_input_tokens=100,
            reasoning_output_tokens=20,
            model_requests=7,
        )
        assert usage.cache_read_input_tokens == 600

    def test_a_pre_split_claude_record_still_validates(self) -> None:
        # Before agentshim 0.7, cached_input_tokens counted cache writes too.
        usage = UsageMetrics(
            llm_calls=4, input_tokens=800, output_tokens=75, cached_input_tokens=300, cache_write_input_tokens=100
        )
        assert usage.cache_read_input_tokens is None

    @pytest.mark.parametrize(
        ("fields", "match"),
        [
            ({"cached_input_tokens": 600, "cache_read_input_tokens": 500}, "alias"),
            ({"cache_read_input_tokens": 800, "cache_write_input_tokens": 300}, "exceed input_tokens"),
            ({"cache_read_input_tokens": 600, "uncached_input_tokens": 1}, "uncached_input_tokens"),
            (
                {"cache_read_input_tokens": 0, "cache_write_input_tokens": 10, "cache_write_1h_input_tokens": 11},
                "cache_write_1h_input_tokens",
            ),
            ({"reasoning_output_tokens": 81}, "reasoning_output_tokens"),
        ],
    )
    def test_an_inconsistent_breakdown_is_rejected(self, fields: dict[str, int], match: str) -> None:
        with pytest.raises(ValueError, match=match):
            UsageMetrics(llm_calls=1, input_tokens=1_000, output_tokens=80, **fields)
