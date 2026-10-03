from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sdo.contracts import (
    ConfirmedRootCause,
    DetectorEvaluation,
    Finding,
    RepairActionReceipt,
    UsageMetrics,
)
from sdo.operational_memory.diagnosis import RootCauseVerification  # noqa: TC001 - resolved at runtime.

MEMORY_SCHEMA_VERSION = 1
#: Playbook ``fault_class`` values, also the playbook directory naming convention.
FAULT_CLASS_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
#: Detector registration IDs in the diagnostics manifest.
DETECTOR_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"

#: Largest ``persistence.firing`` the validator accepts for a new or changed
#: incident detector: a learned fault signature must fire on its first match so
#: its playbook is surfaced at dispatch rather than after the health detectors.
INCIDENT_DETECTOR_MAX_FIRING = 1


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidatorNetworkPolicyCanary(MemoryModel):
    """Durable evidence that validator NetworkPolicy isolation was exercised."""

    mode: Literal["allow", "deny"]
    passed: bool
    job_name: str = Field(min_length=1)
    observed_at: datetime
    details: str = Field(min_length=1)


class ArtifactOwner(str, Enum):
    HUMAN = "human"
    DEPLOYER = "deployer"
    HEALTH_JUDGE = "health_judge"
    RESPONDER = "responder"
    CONTROLLER = "controller"
    UPKEEP = "upkeep"


class GoalMetadata(MemoryModel):
    schema_version: Literal[1] = MEMORY_SCHEMA_VERSION
    owner: Literal[ArtifactOwner.HUMAN] = ArtifactOwner.HUMAN
    application: str = Field(min_length=1)


class ArchitectureMetadata(MemoryModel):
    schema_version: Literal[1] = MEMORY_SCHEMA_VERSION
    generated_at_commit: str = Field(min_length=1)
    generated_at: datetime
    application: str = Field(min_length=1)
    topology_fingerprint: str = Field(min_length=1)


class PlaybookMetadata(MemoryModel):
    schema_version: Literal[1] = MEMORY_SCHEMA_VERSION
    owner: Literal[ArtifactOwner.RESPONDER] = ArtifactOwner.RESPONDER
    fault_class: str = Field(min_length=1, pattern=FAULT_CLASS_PATTERN)
    originating_incident: str = Field(min_length=1)
    originating_commit: str | None = Field(default=None, min_length=1)


class DetectorWatch(MemoryModel):
    api_version: str = Field(alias="apiVersion", min_length=1)
    kind: str = Field(min_length=1)
    namespace: str = ""


class DetectorPersistence(MemoryModel):
    firing: int = Field(ge=1)
    clearing: int = Field(ge=1)


class DetectorBatching(MemoryModel):
    severity: Literal["info", "warn", "critical"]
    debounce: str = Field(pattern=r"^(?:0|(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+)$")


class DetectorRegistration(MemoryModel):
    id: str = Field(pattern=DETECTOR_ID_PATTERN)
    package: str = Field(min_length=1)
    constructor: str = "New"
    detector_class: Literal["health", "incident"] = Field(alias="class")
    owner: Literal[ArtifactOwner.HEALTH_JUDGE, ArtifactOwner.RESPONDER]
    watches: list[DetectorWatch]
    interval: str = Field(pattern=r"^(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+$")
    persistence: DetectorPersistence
    batching: DetectorBatching
    possible_playbooks: list[str] = Field(alias="possiblePlaybooks")
    originating_incident: str | None = Field(default=None, alias="originatingIncident")
    originating_commit: str = Field(alias="originatingCommit", min_length=1)

    @field_validator("constructor")
    @classmethod
    def validate_constructor(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError("constructor must be an identifier")
        return value

    @model_validator(mode="after")
    def validate_registration(self) -> DetectorRegistration:
        expected_owner = ArtifactOwner.HEALTH_JUDGE if self.detector_class == "health" else ArtifactOwner.RESPONDER
        if self.owner != expected_owner:
            raise ValueError(f"class {self.detector_class!r} must be owned by {expected_owner.value!r}")
        if self.detector_class == "incident" and not self.originating_incident:
            raise ValueError("incident detectors require originatingIncident")
        watch_keys = [(watch.api_version, watch.kind, watch.namespace) for watch in self.watches]
        if len(watch_keys) != len(set(watch_keys)):
            raise ValueError("detector watches must be unique")
        if len(self.possible_playbooks) != len(set(self.possible_playbooks)):
            raise ValueError("possiblePlaybooks must be unique")
        return self


class DiagnosticsManifest(MemoryModel):
    api_version: str = Field(alias="apiVersion")
    kind: str
    sdk_version: str = Field(alias="sdkVersion", min_length=1)
    detectors: list[DetectorRegistration] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_header_and_ids(self) -> DiagnosticsManifest:
        if self.api_version != "sdo.dev/v1alpha1":
            raise ValueError("apiVersion must be sdo.dev/v1alpha1")
        if self.kind != "DetectorManifest":
            raise ValueError("kind must be DetectorManifest")
        ids = [detector.id for detector in self.detectors]
        if len(ids) != len(set(ids)):
            raise ValueError("detector IDs must be unique")
        return self


#: Synthetic traffic lives under ``.sdo/diagnostics/traffic/``: Go generators
#: (``generators/``, with responder-owned ``generators/incident/``) and
#: workload profiles (``workloads/<name>.yaml``; responders add only
#: ``incident-*`` workloads). ``controller/sdk/traffic`` executes them; the
#: proto ``TrafficWorkload`` schema and :mod:`sdo.operational_memory.traffic`
#: loader are the commit-time gate.
TRAFFIC_DIRECTORY = ".sdo/diagnostics/traffic"
TRAFFIC_GENERATORS_DIRECTORY = f"{TRAFFIC_DIRECTORY}/generators"
TRAFFIC_INCIDENT_GENERATORS_DIRECTORY = f"{TRAFFIC_GENERATORS_DIRECTORY}/incident"
TRAFFIC_WORKLOAD_DIRECTORY = f"{TRAFFIC_DIRECTORY}/workloads"
TRAFFIC_INCIDENT_WORKLOAD_PREFIX = "incident-"


class OutcomeClassification(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    #: Health cleared in time, but the responder's own repair backs none of
    #: its confirmed causes: someone else recovered the incident (F8).
    EXTERNAL_RECOVERY = "external_recovery"
    #: Health was verified clear but the responder made no successful change (a
    #: transient that healed on its own, or a responder that only observed): not a
    #: mitigation, never learned from (D30).
    CLEARED_WITHOUT_ACTION = "cleared_without_action"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutcomeTimestamps(MemoryModel):
    detected_at: datetime
    dispatched_at: datetime
    mitigated_at: datetime | None = None
    verified_at: datetime | None = None
    completed_at: datetime

    @model_validator(mode="after")
    def validate_order(self) -> OutcomeTimestamps:
        ordered = [self.detected_at, self.dispatched_at]
        ordered.extend(value for value in (self.mitigated_at, self.verified_at) if value is not None)
        ordered.append(self.completed_at)
        if any(right < left for left, right in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("outcome timestamps must be chronological")
        return self


class OutcomeRecord(MemoryModel):
    schema_version: Literal[1] = MEMORY_SCHEMA_VERSION
    incident_id: str = Field(min_length=1)
    source_commit: str = Field(min_length=1)
    deployed_commit: str = Field(min_length=1)
    detector_history: list[DetectorEvaluation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    surfaced_playbooks: list[str] = Field(default_factory=list)
    inspected_playbooks: list[str] = Field(default_factory=list)
    confirmed_playbooks: list[str] = Field(default_factory=list)
    rejected_playbooks: list[str] = Field(default_factory=list)
    applied_playbooks: list[str] = Field(default_factory=list)
    confirmed_root_causes: list[ConfirmedRootCause] = Field(default_factory=list)
    final_health_detector_state: list[DetectorEvaluation] = Field(default_factory=list)
    classification: OutcomeClassification
    repair_commit: str | None = None
    repair_actions: list[RepairActionReceipt] = Field(default_factory=list)
    memory_commit: str | None = None
    responder_backend: str = Field(min_length=1)
    responder_model: str = Field(min_length=1)
    usage: UsageMetrics = Field(default_factory=lambda: UsageMetrics(llm_calls=0, input_tokens=0, output_tokens=0))
    timestamps: OutcomeTimestamps
    # Deterministic check of each root cause's evidence and explained
    # detectors; empty in outcomes recorded before diagnoses carried evidence.
    diagnosis_verification: list[RootCauseVerification] = Field(default_factory=list)
