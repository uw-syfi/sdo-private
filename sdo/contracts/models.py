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
    related_resources: list[ObjectRef] = Field(default_factory=list)
    playbooks: list[str] = Field(default_factory=list)
    parameter_bindings: dict[str, ObjectRef] = Field(default_factory=dict)
    fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DetectorEvaluation(ContractModel):
    detector_id: str = Field(min_length=1)
    evaluated_at: datetime
    status: DetectorEvaluationStatus
    fingerprints: list[str] = Field(default_factory=list)
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
    surfaced_playbooks: list[SurfacedPlaybook] = Field(default_factory=list)
    source_commit: str = Field(min_length=1)
    deployed_commit: str = Field(min_length=1)
    architecture_summary_path: str = Field(min_length=1)
    health_objective_path: str = Field(min_length=1)
    repository_worktree: str = Field(min_length=1)
    repository_base_commit: str = Field(min_length=1)
    response_deadline: datetime
    cancellation_token: str = Field(min_length=1)
    repair_policy: Literal["commit", "recorded-actions"] = "commit"


class ConfirmedRootCause(ContractModel):
    summary: str = Field(min_length=1)
    resources: list[ObjectRef] = Field(min_length=1)


class AppliedPlaybook(SurfacedPlaybook):
    scripts: list[str] = Field(default_factory=list)


class VerificationEvidence(ContractModel):
    name: str = Field(min_length=1)
    passed: bool
    details: str = Field(min_length=1)
    observed_at: datetime


class UsageMetrics(ContractModel):
    llm_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_cached_input(self) -> UsageMetrics:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens must not exceed input_tokens")
        if self.cache_write_input_tokens > self.cached_input_tokens:
            raise ValueError("cache_write_input_tokens must not exceed cached_input_tokens")
        return self


class TimingMetrics(ContractModel):
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_order(self) -> TimingMetrics:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        return self


class RepairActionReceipt(ContractModel):
    action_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    target: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    details: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    success: bool
    reversible: bool

    @model_validator(mode="after")
    def validate_order(self) -> RepairActionReceipt:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        return self


class IncidentResult(ContractModel):
    schema_version: Literal["sdo.dev/v1alpha1"] = SCHEMA_VERSION
    incident_id: str = Field(min_length=1)
    status: IncidentStatus
    confirmed_root_causes: list[ConfirmedRootCause] = Field(default_factory=list)
    applied_playbooks: list[AppliedPlaybook] = Field(default_factory=list)
    repair_changes: list[str] = Field(default_factory=list)
    repair_actions: list[RepairActionReceipt] = Field(default_factory=list)
    final_detector_states: list[DetectorEvaluation] = Field(default_factory=list)
    proposed_memory_changes: list[str] = Field(default_factory=list)
    verification_evidence: list[VerificationEvidence] = Field(default_factory=list)
    usage: UsageMetrics
    timing: TimingMetrics
    responder_session_id: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_repair_action_ids(self) -> IncidentResult:
        action_ids = [action.action_id for action in self.repair_actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("repair action IDs must be unique")
        return self
