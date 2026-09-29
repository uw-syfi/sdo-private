"""Strict receipt validity for incidents SDO did not mitigate.

The fixtures are the five receipts the phase-1 live run rejected (assurance
``PHASE1_RESULTS.md``), copied without their runtime-artifact paths. Three had
``recovery_attribution=external`` and no reflection (B2, B9, C8); two were
cancelled no-ops whose health cleared before any repair (C5, D9).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.sregym.protocol import (
    ProductionReceiptValidationError,
    receipt_resolution,
    validate_production_receipt,
)

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "sregym" / "phase1_rejected_receipts"


def _receipt(name: str) -> dict[str, Any]:
    document = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return document["receipt"]


@pytest.mark.parametrize("name", ["b2", "b9", "c8"])
def test_an_externally_recovered_incident_has_a_valid_receipt_that_is_not_a_mitigation(name: str) -> None:
    receipt = _receipt(name)
    assert receipt["recovery_attribution"] == "external"
    assert receipt["reflection_commit"] is None

    validate_production_receipt(receipt)

    assert receipt_resolution(receipt) == "external_recovery"


@pytest.mark.parametrize("name", ["c5", "d9"])
def test_a_cancelled_no_op_that_cleared_on_its_own_has_a_valid_receipt_that_is_not_a_mitigation(name: str) -> None:
    receipt = _receipt(name)
    assert receipt["completed"] is False
    assert receipt["repair_actions"] == []

    validate_production_receipt(receipt)

    assert receipt_resolution(receipt) == "cleared_without_sdo_action"


@pytest.mark.parametrize("name", ["b2", "b9", "c8", "c5", "d9"])
def test_the_resolution_field_must_agree_with_the_receipt_evidence(name: str) -> None:
    receipt = _receipt(name)

    validate_production_receipt({**receipt, "resolution": receipt_resolution(receipt)})
    with pytest.raises(ProductionReceiptValidationError, match="resolution"):
        validate_production_receipt({**receipt, "resolution": "sdo_mitigated"})
    with pytest.raises(ProductionReceiptValidationError, match="resolution"):
        validate_production_receipt({**receipt, "resolution": "healed"})


def test_a_mitigation_claim_still_requires_completion_and_same_session_reflection() -> None:
    external = _receipt("b2")
    mitigated = {**external, "recovery_attribution": "responder"}
    assert receipt_resolution(mitigated) == "sdo_mitigated"

    with pytest.raises(ProductionReceiptValidationError, match="same_session_reflection"):
        validate_production_receipt(mitigated)
    with pytest.raises(ProductionReceiptValidationError, match="reflection_commit"):
        validate_production_receipt({**mitigated, "same_session_reflection": True})
    reflected = {
        **mitigated,
        "same_session_reflection": True,
        "reflection_commit": "reflection",
        "validator_evidence_commit": "reflection",
    }
    validate_production_receipt(reflected)
    with pytest.raises(ProductionReceiptValidationError, match="completed=true"):
        validate_production_receipt({**reflected, "completed": False})


def test_a_repair_that_ran_but_was_not_completed_is_still_rejected() -> None:
    # A responder that recorded a successful repair and then reported "cancelled"
    # has not cleared without action: it mutated the cluster and must say completed.
    receipt = {**_receipt("b2"), "recovery_attribution": None, "completed": False}

    assert receipt_resolution(receipt) == "sdo_mitigated"
    with pytest.raises(ProductionReceiptValidationError, match="completed=true"):
        validate_production_receipt(receipt)


def test_a_non_mitigated_receipt_keeps_the_integrity_checks() -> None:
    receipt = _receipt("c5")

    for field, value, match in (
        ("acknowledged", False, "acknowledged"),
        ("cleaned", False, "cleaned"),
        ("remaining_worktrees", ["leftover"], "remaining_worktrees"),
        ("independent_verification", [{"passed": False}], "independent_verification"),
        ("detector_clear", [{"status": "clear", "fingerprints": ["stale"]}], "detector_clear"),
        ("outcome_commit", None, "outcome_commit"),
        ("validator_evidence_commit", "other", "validator_evidence_commit"),
    ):
        with pytest.raises(ProductionReceiptValidationError, match=match):
            validate_production_receipt({**receipt, field: value})
