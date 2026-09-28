from __future__ import annotations

import re
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
#: workload model below mirrors its validation as the commit-time gate.
TRAFFIC_DIRECTORY = ".sdo/diagnostics/traffic"
TRAFFIC_GENERATORS_DIRECTORY = f"{TRAFFIC_DIRECTORY}/generators"
TRAFFIC_INCIDENT_GENERATORS_DIRECTORY = f"{TRAFFIC_GENERATORS_DIRECTORY}/incident"
TRAFFIC_WORKLOAD_DIRECTORY = f"{TRAFFIC_DIRECTORY}/workloads"
TRAFFIC_INCIDENT_WORKLOAD_PREFIX = "incident-"
TRAFFIC_MAX_RATE_PER_SECOND = 20.0
TRAFFIC_MAX_TIMEOUT_SECONDS = 10.0
TRAFFIC_MAX_ITERATION_TIMEOUT_SECONDS = 30.0
TRAFFIC_MAX_BURST_SECONDS = 60.0
_GO_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_GO_DURATION_SECONDS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}
_DNS_LABEL = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"


def go_duration_seconds(value: str) -> float:
    """Seconds in a Go duration string such as ``1m30s`` or ``500ms``."""

    parts = list(_GO_DURATION_PART.finditer(value))
    if not parts or "".join(part.group(0) for part in parts) != value:
        raise ValueError(f"{value!r} is not a Go duration such as 2s or 500ms")
    return sum(float(part.group(1)) * _GO_DURATION_SECONDS[part.group(2)] for part in parts)


class TrafficModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TrafficSLO(TrafficModel):
    """Per-scenario service-level objective; unset fields inherit the workload, then defaults."""

    window: int | None = Field(default=None, ge=1, le=100)
    min_samples: int | None = Field(default=None, ge=1, alias="minSamples")
    max_age: str | None = Field(default=None, alias="maxAge")
    max_error_rate: float | None = Field(default=None, gt=0, le=1, alias="maxErrorRate")
    max_timeout_rate: float | None = Field(default=None, gt=0, le=1, alias="maxTimeoutRate")
    latency_percentile: int | None = Field(default=None, ge=1, le=100, alias="latencyPercentile")
    max_latency: str | None = Field(default=None, alias="maxLatency")

    @field_validator("max_age", "max_latency")
    @classmethod
    def validate_duration(cls, value: str | None) -> str | None:
        if value is not None and go_duration_seconds(value) <= 0:
            raise ValueError("duration must be positive")
        return value


#: Defaults applied by ``controller/sdk/traffic`` when a workload leaves them unset.
TRAFFIC_DEFAULT_SLO = TrafficSLO(
    window=5,
    minSamples=3,
    maxAge="30s",
    maxErrorRate=0.5,
    maxTimeoutRate=0.5,
    latencyPercentile=90,
    maxLatency="1500ms",
)


def _merge_slo(base: TrafficSLO, override: TrafficSLO | None) -> TrafficSLO:
    if override is None:
        return base
    return base.model_copy(update=override.model_dump(exclude_none=True))


class TrafficWorkloadScenario(TrafficModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    weight: int = Field(default=1, ge=1, le=100)
    slo: TrafficSLO | None = None


class TrafficWorkload(TrafficModel):
    """A workload profile: which generator scenarios run, how often, and against which SLO.

    Stored at ``.sdo/diagnostics/traffic/workloads/<name>.yaml``. A
    ``health-probe`` runs continuously and feeds traffic health detectors; a
    ``verify-burst`` runs for a few seconds on demand to verify a repair; a
    ``journey`` runs on demand for a bounded time.
    """

    api_version: Literal["sdo.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["TrafficWorkload"]
    name: str = Field(pattern=_DNS_LABEL)
    description: str = ""
    purpose: Literal["health-probe", "verify-burst", "journey"]
    arrival: Literal["uniform", "poisson"] = "uniform"
    rate_per_second: float = Field(default=4.0, gt=0, le=TRAFFIC_MAX_RATE_PER_SECOND, alias="ratePerSecond")
    duration: str | None = None
    timeout: str = "2s"
    iteration_timeout: str = Field(default="10s", alias="iterationTimeout")
    seed: int | None = Field(default=None, ge=0, lt=2**64)
    slo: TrafficSLO = Field(default_factory=TrafficSLO)
    scenarios: list[TrafficWorkloadScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile(self) -> TrafficWorkload:
        if self.purpose == "health-probe":
            if self.duration is not None:
                raise ValueError("a health-probe runs continuously and takes no duration")
        elif self.duration is None or not 0 < go_duration_seconds(self.duration) <= TRAFFIC_MAX_BURST_SECONDS:
            raise ValueError(f"a {self.purpose} workload needs a duration in (0, 60s]")
        timeout = go_duration_seconds(self.timeout)
        if not 0 < timeout <= TRAFFIC_MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout must be in (0, 10s]")
        if not timeout <= go_duration_seconds(self.iteration_timeout) <= TRAFFIC_MAX_ITERATION_TIMEOUT_SECONDS:
            raise ValueError("iterationTimeout must be in [timeout, 30s]")
        ids = [scenario.id for scenario in self.scenarios]
        duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
        if duplicates:
            raise ValueError(f"scenario(s) listed twice: {', '.join(duplicates)}")
        for scenario in self.scenarios:
            slo = self.scenario_slo(scenario.id)
            if slo.min_samples is not None and slo.window is not None and slo.min_samples > slo.window:
                raise ValueError(f"scenario {scenario.id!r}: slo minSamples must not exceed window")
        return self

    def scenario_slo(self, scenario_id: str) -> TrafficSLO:
        slo = _merge_slo(TRAFFIC_DEFAULT_SLO, self.slo)
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                slo = _merge_slo(slo, scenario.slo)
        return slo


class OutcomeClassification(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    #: Health cleared in time, but the responder's own repair backs none of
    #: its confirmed causes: someone else recovered the incident (F8).
    EXTERNAL_RECOVERY = "external_recovery"
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
