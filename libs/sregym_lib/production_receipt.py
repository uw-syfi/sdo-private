"""Canonical validation for standalone SDO production receipts.

This module is deliberately in the dependency-free SREGym integration layer:
both the generic pipeline gate and the application-specific runtime must apply
the exact same artifact contract without introducing a dependency cycle.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- Pydantic resolves this annotation at runtime.
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator


class ProductionReceiptValidationError(ValueError):
    """Raised when an SDO receipt cannot prove production completion."""


class _ControllerRolloutRecord(BaseModel):
    """Cross-layer schema for rollout evidence embedded in a receipt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sdo.controller-rollout/v1"] = "sdo.controller-rollout/v1"
    incident_id: str = Field(min_length=1)
    reflection_commit: str = Field(min_length=1)
    before_detector_fingerprint: str = Field(min_length=1)
    after_detector_fingerprint: str = Field(min_length=1)
    controller_job: str = Field(min_length=1)
    controller_pod_uid: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    returncode: StrictInt
    success: bool

    @model_validator(mode="after")
    def validate_attempt(self) -> _ControllerRolloutRecord:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.success != (self.returncode == 0):
            raise ValueError("success must be true exactly when returncode is zero")
        return self


def validate_production_receipt(receipt: dict[str, Any], *, allow_test_lifecycle: bool = False) -> None:
    """Validate the strict, standalone SDO production receipt contract."""

    if receipt.get("schema_version") != "sdo.production-receipt/v1":
        raise ProductionReceiptValidationError("invalid production receipt schema_version")
    if receipt.get("pre_cutover") is not False:
        raise ProductionReceiptValidationError("production receipt must exclude pre-cutover evidence")
    if receipt.get("validator_mode") != "kubernetes-job":
        raise ProductionReceiptValidationError("production receipt requires validator_mode=kubernetes-job")
    for field in (
        "production_job_dispatch",
        "completed",
        "same_session_reflection",
        "acknowledged",
        "cleaned",
    ):
        if receipt.get(field) is not True:
            raise ProductionReceiptValidationError(f"production receipt requires {field}=true")
    lifecycle_provenance = receipt.get("lifecycle_provenance")
    if lifecycle_provenance is not True and not (allow_test_lifecycle and lifecycle_provenance is False):
        raise ProductionReceiptValidationError("production receipt requires lifecycle_provenance=true")
    if receipt.get("controller_update_required") is True and receipt.get("controller_update_rollout") is not True:
        raise ProductionReceiptValidationError("production receipt requires controller_update_rollout=true")
    if receipt.get("controller_update_required") is True:
        try:
            rollout = _ControllerRolloutRecord.model_validate(receipt.get("controller_update_rollout_record"))
        except ValidationError as exc:
            raise ProductionReceiptValidationError(
                "production receipt requires a valid durable rollout record"
            ) from exc
        if not rollout.success or rollout.returncode != 0:
            raise ProductionReceiptValidationError("production receipt durable rollout record requires returncode=0")
        if rollout.incident_id != receipt.get("incident_id"):
            raise ProductionReceiptValidationError("production receipt rollout incident_id mismatch")
        if rollout.reflection_commit != receipt.get("reflection_commit"):
            raise ProductionReceiptValidationError("production receipt rollout reflection_commit mismatch")
    for field in ("proposal_commit", "outcome_commit", "reflection_commit"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ProductionReceiptValidationError(f"production receipt requires {field}")
    if receipt.get("validator_evidence_commit") != receipt["reflection_commit"]:
        raise ProductionReceiptValidationError(
            "production receipt requires validator_evidence_commit=reflection_commit"
        )
    detector_clear = receipt.get("detector_clear")
    if (
        not isinstance(detector_clear, list)
        or not detector_clear
        or any(
            not isinstance(state, dict) or state.get("status") != "clear" or state.get("fingerprints") != []
            for state in detector_clear
        )
    ):
        raise ProductionReceiptValidationError("production receipt requires detector_clear with empty fingerprints")
    verification = receipt.get("independent_verification")
    if (
        not isinstance(verification, list)
        or not verification
        or any(not isinstance(evidence, dict) or evidence.get("passed") is not True for evidence in verification)
    ):
        raise ProductionReceiptValidationError("production receipt requires passing independent_verification")
    canaries = receipt.get("validator_network_policy_canaries")
    if not isinstance(canaries, list) or len(canaries) != 2:
        raise ProductionReceiptValidationError("production receipt requires validator_network_policy_canaries")
    if any(not isinstance(canary, dict) or canary.get("passed") is not True for canary in canaries):
        raise ProductionReceiptValidationError("production receipt requires network-policy canaries passed=true")
    if {canary.get("mode") for canary in canaries} != {"allow", "deny"}:
        raise ProductionReceiptValidationError(
            "production receipt requires exactly one allow and one deny network-policy canary"
        )
    for canary in canaries:
        for field in ("job_name", "observed_at", "details"):
            if not isinstance(canary.get(field), str) or not canary[field].strip():
                raise ProductionReceiptValidationError(f"production receipt network-policy canary requires {field}")
    if receipt.get("remaining_worktrees") != []:
        raise ProductionReceiptValidationError("production receipt requires remaining_worktrees=[]")
    responder_jobs = receipt.get("responder_jobs")
    if not isinstance(responder_jobs, list) or len(responder_jobs) != 1:
        raise ProductionReceiptValidationError("production receipt requires exactly one responder Job")
