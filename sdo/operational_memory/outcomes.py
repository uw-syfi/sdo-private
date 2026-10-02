from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sdo.contracts import (
    DetectorEvaluation,
    IncidentRequest,
    IncidentResult,
    IncidentStatus,
    ObservedStateChange,
    StateChanges,
    UsageMetrics,
)
from sdo.operational_memory.diagnosis import RootCauseVerification, recovered_by_responder, verify_diagnosis
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
    # The controller's configuration diff against the healthy baseline as of
    # verification time (its closing view). It can name a composite's later
    # fault that landed after the request's dispatch-time diff was taken
    # (N11); ``verify_diagnosis`` checks state-change evidence against both.
    final_state_changes: StateChanges | None = None
    # When the controller saw the health detectors begin their final clear
    # streak. A repair action backs a root cause only if it started by then
    # (F8). None from controllers that predate it.
    health_cleared_at: datetime | None = None
    # Every object the controller saw differ from the baseline while the
    # incident was open, with when it first saw it. A state-change citation
    # must predate the responder's own repair of it. None from controllers
    # that predate it.
    observed_state_changes: list[ObservedStateChange] | None = None
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
    result = facts.result
    verification = verify_diagnosis(
        facts.request,
        result,
        final_detector_states=facts.final_health_detector_state,
        incident_detector_states=facts.incident_detector_states,
        final_state_changes=facts.final_state_changes,
        health_cleared_at=facts.health_cleared_at,
        observed_state_changes=facts.observed_state_changes,
        dispatched_at=facts.dispatched_at,
        responder_completed_at=facts.responder_completed_at,
    )
    classification = _classification(facts, verification)
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
        diagnosis_verification=verification,
    )


def _classification(facts: OutcomeFacts, verification: list[RootCauseVerification]) -> OutcomeClassification:
    if facts.missed_fault_detected:
        return OutcomeClassification.FALSE_NEGATIVE
    if facts.dispatch_error or facts.result is None or facts.result.status == IncidentStatus.FAILED:
        return OutcomeClassification.FAILED
    if not facts.health_verified:
        if facts.result.status == IncidentStatus.CANCELLED:
            return OutcomeClassification.CANCELLED
        return OutcomeClassification.PARTIAL
    if _no_recorded_repair(facts):
        return OutcomeClassification.CLEARED_WITHOUT_ACTION
    if facts.result.status == IncidentStatus.CANCELLED:
        return OutcomeClassification.CANCELLED
    if not facts.fault_confirmed:
        return OutcomeClassification.FALSE_POSITIVE
    if recovered_by_responder(verification) is False:
        # Health cleared in time, but the responder's own repair backs none of
        # its causes: someone else recovered the incident (F8).
        return OutcomeClassification.EXTERNAL_RECOVERY
    return OutcomeClassification.SUCCESS


def _no_recorded_repair(facts: OutcomeFacts) -> bool:
    """A responder that changed nothing while health was already clear.

    A responder that finds an already-healed transient (for example a stray
    self-healed data-plane stall) reports ``completed`` or ``cancelled`` with
    no repository commit and no successful repair action. It is a no-op, not
    a mitigation: crediting it as SUCCESS or FALSE_POSITIVE would be wrong,
    and reflecting on it would learn a playbook from nothing. This is
    reachable only when health is already verified clear (checked by the
    caller), so a claim of completion with no action while health is still
    bad is never reclassified here; the broker rejects that before an outcome
    exists (see ``BrokerService._validate_recorded_actions`` and
    CHAOS_DECISIONS.md F16). The classification is
    ``CLEARED_WITHOUT_ACTION`` so reports never count it as a mitigation (D30).
    """

    return (
        facts.result is not None
        and facts.result.status in (IncidentStatus.COMPLETED, IncidentStatus.CANCELLED)
        and facts.repair_commit is None
        and not any(action.success for action in facts.result.repair_actions)
    )
