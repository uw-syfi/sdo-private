from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sdo.contracts import (
    DetectorEvaluation,
    IncidentRequest,
    IncidentResult,
    IncidentStatus,
    UsageMetrics,
)
from sdo.operational_memory.diagnosis import verify_diagnosis
from sdo.operational_memory.models import OutcomeClassification, OutcomeRecord, OutcomeTimestamps


class OutcomeFacts(BaseModel):
    """Controller-owned facts used to classify a completed incident."""

    model_config = ConfigDict(extra="forbid")

    request: IncidentRequest
    result: IncidentResult | None = None
    dispatch_error: str | None = None
    final_health_detector_state: list[DetectorEvaluation] = Field(default_factory=list)
    # Post-response evaluations of the incident's non-health detectors.
    incident_detector_states: list[DetectorEvaluation] = Field(default_factory=list)
    health_verified: bool
    fault_confirmed: bool
    missed_fault_detected: bool = False
    inspected_playbooks: list[str] = Field(default_factory=list)
    confirmed_playbooks: list[str] = Field(default_factory=list)
    rejected_playbooks: list[str] = Field(default_factory=list)
    repair_commit: str | None = None
    memory_commit: str | None = None
    responder_backend: str = Field(min_length=1)
    responder_model: str = Field(min_length=1)
    detected_at: datetime
    dispatched_at: datetime
    responder_completed_at: datetime
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_incident_and_verification(self) -> OutcomeFacts:
        if self.result is not None and self.result.incident_id != self.request.incident_id:
            raise ValueError("result incident_id must match request")
        if self.health_verified and self.verified_at is None:
            raise ValueError("verified_at is required when health_verified is true")
        return self


def derive_outcome(facts: OutcomeFacts) -> OutcomeRecord:
    classification = _classification(facts)
    result = facts.result
    applied_playbooks = [] if result is None else [playbook.path for playbook in result.applied_playbooks]
    completed_at = facts.verified_at if facts.health_verified else facts.responder_completed_at
    if completed_at is None:  # verified incidents are validated to have verified_at.
        completed_at = facts.responder_completed_at
    return OutcomeRecord(
        incident_id=facts.request.incident_id,
        source_commit=facts.request.source_commit,
        deployed_commit=facts.request.deployed_commit,
        detector_history=facts.request.detector_history,
        findings=facts.request.findings,
        surfaced_playbooks=[playbook.path for playbook in facts.request.surfaced_playbooks],
        inspected_playbooks=facts.inspected_playbooks,
        confirmed_playbooks=facts.confirmed_playbooks,
        rejected_playbooks=facts.rejected_playbooks,
        applied_playbooks=applied_playbooks,
        confirmed_root_causes=[] if result is None else result.confirmed_root_causes,
        final_health_detector_state=facts.final_health_detector_state,
        classification=classification,
        repair_commit=facts.repair_commit,
        repair_actions=[] if result is None else result.repair_actions,
        memory_commit=facts.memory_commit,
        responder_backend=facts.responder_backend,
        responder_model=facts.responder_model,
        usage=UsageMetrics(llm_calls=0, input_tokens=0, output_tokens=0) if result is None else result.usage,
        timestamps=OutcomeTimestamps(
            detected_at=facts.detected_at,
            dispatched_at=facts.dispatched_at,
            mitigated_at=facts.responder_completed_at if result is not None else None,
            verified_at=facts.verified_at if facts.health_verified else None,
            completed_at=completed_at,
        ),
        diagnosis_verification=verify_diagnosis(
            facts.request,
            result,
            final_detector_states=facts.final_health_detector_state,
            incident_detector_states=facts.incident_detector_states,
        ),
    )


def _classification(facts: OutcomeFacts) -> OutcomeClassification:
    if facts.missed_fault_detected:
        return OutcomeClassification.FALSE_NEGATIVE
    if facts.dispatch_error or facts.result is None or facts.result.status == IncidentStatus.FAILED:
        return OutcomeClassification.FAILED
    if facts.result.status == IncidentStatus.CANCELLED:
        return OutcomeClassification.CANCELLED
    if not facts.health_verified:
        return OutcomeClassification.PARTIAL
    if not facts.fault_confirmed:
        return OutcomeClassification.FALSE_POSITIVE
    return OutcomeClassification.SUCCESS
