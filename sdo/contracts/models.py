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


class PriorOutcomeEvidence(ContractModel):
    incident_id: str = Field(min_length=1)
    # learned-detector-origin: a detector learned from this outcome fired on the same resource.
    match_reason: Literal["exact-fingerprint", "learned-detector-origin", "detector-rule-resource-kind"]
    root_cause_summaries: list[str] = Field(default_factory=list)
    repair_action_summaries: list[str] = Field(default_factory=list)
    applied_playbooks: list[str] = Field(default_factory=list)
    source_commit: str = Field(min_length=1)
    exact_source_match: bool


class StateFieldChange(ContractModel):
    """One changed aspect of an object; data values appear only as digests.

    Optional fields mirror the Go runtime's ``omitempty`` encoding.
    """

    field: str = Field(min_length=1)
    before: str | None = None
    after: str | None = None


class StateChange(ContractModel):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    change: Literal["added", "removed", "modified"]
    fields: list[StateFieldChange] | None = None


class StateChanges(ContractModel):
    """The application's configuration diff against its last healthy baseline."""

    baseline_at: datetime
    observed_at: datetime
    changes: list[StateChange] = Field(default_factory=list)
    omitted: int | None = Field(default=None, ge=0)
    unobserved_kinds: list[str] | None = None


class IncidentView(ContractModel):
    """The open incident as the controller sees it at its latest evaluation.

    Published in the controller's state ConfigMap (Go ``runtime.IncidentView``)
    while an incident is open. The request is a snapshot from dispatch; the
    view shows what still blocks closure and the configuration diff now.
    """

    incident_id: str = Field(min_length=1)
    observed_at: datetime
    blocking_detectors: list[str] = Field(default_factory=list)
    blocking_findings: list[Finding] = Field(default_factory=list)
    state_changes: StateChanges | None = None


class IncidentRequest(ContractModel):
    schema_version: Literal["sdo.dev/v1alpha1"] = SCHEMA_VERSION
    application: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
    detector_history: list[DetectorEvaluation] = Field(min_length=1)
    surfaced_playbooks: list[SurfacedPlaybook] = Field(default_factory=list)
    relevant_outcomes: list[PriorOutcomeEvidence] = Field(default_factory=list, max_length=3)
    source_commit: str = Field(min_length=1)
    deployed_commit: str = Field(min_length=1)
    architecture_summary_path: str = Field(min_length=1)
    health_objective_path: str = Field(min_length=1)
    repository_worktree: str = Field(min_length=1)
    repository_base_commit: str = Field(min_length=1)
    response_deadline: datetime
    cancellation_token: str = Field(min_length=1)
    repair_policy: Literal["commit", "recorded-actions"] = "commit"
    state_changes: StateChanges | None = None


#: Live evidence kinds a root cause may cite. Static artifacts (manifests,
#: scripts, ConfigMap bodies, source files, architecture notes) show what
#: could go wrong, not what did, so they are only ``static_context``.
ROOT_CAUSE_EVIDENCE_KINDS = ("detector-finding", "synthetic-traffic", "state-change", "live-observation")


class RootCauseEvidence(ContractModel):
    """One live observation that supports a root cause.

    ``source`` names what was observed: a detector ID, a synthetic scenario,
    a changed ``Kind/name`` from the request's state changes, or the command
    that produced ``observation``.
    """

    kind: Literal["detector-finding", "synthetic-traffic", "state-change", "live-observation"]
    source: str = Field(min_length=1)
    observation: str = Field(min_length=1)


class ConfirmedRootCause(ContractModel):
    summary: str = Field(min_length=1)
    resources: list[ObjectRef] = Field(min_length=1)
    # Empty only in records written before evidence was required; the
    # responder's output schema requires at least one live item.
    evidence: list[RootCauseEvidence] = Field(default_factory=list)
    #: Detectors whose findings this cause explains; they must clear after the fix.
    explained_detectors: list[str] = Field(default_factory=list)
    static_context: list[str] = Field(default_factory=list)


class AppliedPlaybook(SurfacedPlaybook):
    scripts: list[str] = Field(default_factory=list)


class VerificationEvidence(ContractModel):
    name: str = Field(min_length=1)
    passed: bool
    details: str = Field(min_length=1)
    observed_at: datetime


class UsageMetrics(ContractModel):
    """Token accounting of one agent turn, in agentshim's normalized breakdown.

    ``input_tokens`` includes cache reads and cache writes;
    ``uncached_input_tokens`` is the rest. ``output_tokens`` includes
    ``reasoning_output_tokens``. ``cached_input_tokens`` is the deprecated
    alias of ``cache_read_input_tokens``. A record without
    ``cache_read_input_tokens`` predates the split (agentshim < 0.7): its
    ``cached_input_tokens`` counted Claude cache writes too, and it is checked
    against that older rule.
    """

    llm_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    cache_write_1h_input_tokens: int | None = Field(default=None, ge=0)
    uncached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    model_requests: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_cached_input(self) -> UsageMetrics:
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("reasoning_output_tokens must not exceed output_tokens")
        if (self.cache_write_1h_input_tokens or 0) > self.cache_write_input_tokens:
            raise ValueError("cache_write_1h_input_tokens must not exceed cache_write_input_tokens")
        read = self.cache_read_input_tokens
        if read is None:
            if self.cached_input_tokens > self.input_tokens:
                raise ValueError("cached_input_tokens must not exceed input_tokens")
            if self.cache_write_input_tokens > self.cached_input_tokens:
                raise ValueError("cache_write_input_tokens must not exceed cached_input_tokens")
            return self
        if self.cached_input_tokens == 0 and read:
            self.cached_input_tokens = read
        if self.cached_input_tokens != read:
            raise ValueError("cached_input_tokens is an alias of cache_read_input_tokens and must equal it")
        if read + self.cache_write_input_tokens > self.input_tokens:
            raise ValueError("cache reads plus cache writes must not exceed input_tokens")
        uncached = self.input_tokens - read - self.cache_write_input_tokens
        if self.uncached_input_tokens is not None and self.uncached_input_tokens != uncached:
            raise ValueError("uncached_input_tokens must equal input minus cache reads and writes")
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
    #: The objects this action mutated. Diagnosis verification credits a
    #: confirmed root cause only to a repair that touched its resources (F8).
    #: Empty in receipts written before it was recorded; ``target`` is then
    #: parsed as ``[namespace/]Kind/name`` instead.
    resources: list[ObjectRef] = Field(default_factory=list)
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
