from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "sdo.dev/v1alpha1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warn"
    CRITICAL = "critical"


class DetectorEvaluationStatus(str, Enum):
    FIRING = "firing"
    CLEAR = "clear"
    ERROR = "error"


class IncidentStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ObjectRef(ContractModel):
    api_version: str = ""
    kind: str = Field(min_length=1)
    namespace: str = ""
    name: str = Field(min_length=1)


class Finding(ContractModel):
    detector_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    status: FindingStatus
    severity: FindingSeverity
    summary: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    primary_resource: ObjectRef
    related_resources: list[ObjectRef] = Field(default_factory=list[ObjectRef])
    playbooks: list[str] = Field(default_factory=list[str])
    parameter_bindings: dict[str, ObjectRef] = Field(default_factory=dict)
    fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DetectorEvaluation(ContractModel):
    detector_id: str = Field(min_length=1)
    evaluated_at: datetime
    status: DetectorEvaluationStatus
    fingerprints: list[str] = Field(default_factory=list[str])
    error: str | None = None


class SurfacedPlaybook(ContractModel):
    path: str = Field(min_length=1)
    parameter_bindings: dict[str, ObjectRef] = Field(default_factory=dict)


class IncidentRequest(ContractModel):
    schema_version: Literal["sdo.dev/v1alpha1"] = SCHEMA_VERSION
    application: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
    detector_history: list[DetectorEvaluation] = Field(min_length=1)
    surfaced_playbooks: list[SurfacedPlaybook] = Field(default_factory=list[SurfacedPlaybook])
    source_commit: str = Field(min_length=1)
    deployed_commit: str = Field(min_length=1)
    architecture_summary_path: str = Field(min_length=1)
    health_objective_path: str = Field(min_length=1)
    repository_worktree: str = Field(min_length=1)
    repository_base_commit: str = Field(min_length=1)
    response_deadline: datetime
    cancellation_token: str = Field(min_length=1)


class ConfirmedRootCause(ContractModel):
    summary: str = Field(min_length=1)
    resources: list[ObjectRef] = Field(min_length=1)


class AppliedPlaybook(SurfacedPlaybook):
    scripts: list[str] = Field(default_factory=list[str])


class VerificationEvidence(ContractModel):
    name: str = Field(min_length=1)
    passed: bool
    details: str = Field(min_length=1)
    observed_at: datetime


class UsageMetrics(ContractModel):
    llm_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class TimingMetrics(ContractModel):
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_order(self) -> TimingMetrics:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        return self


class IncidentResult(ContractModel):
    schema_version: Literal["sdo.dev/v1alpha1"] = SCHEMA_VERSION
    incident_id: str = Field(min_length=1)
    status: IncidentStatus
    confirmed_root_causes: list[ConfirmedRootCause] = Field(default_factory=list[ConfirmedRootCause])
    applied_playbooks: list[AppliedPlaybook] = Field(default_factory=list[AppliedPlaybook])
    repair_changes: list[str] = Field(default_factory=list[str])
    final_detector_states: list[DetectorEvaluation] = Field(default_factory=list[DetectorEvaluation])
    proposed_memory_changes: list[str] = Field(default_factory=list[str])
    verification_evidence: list[VerificationEvidence] = Field(default_factory=list[VerificationEvidence])
    usage: UsageMetrics
    timing: TimingMetrics
    responder_session_id: str | None = None
    error: str | None = None
