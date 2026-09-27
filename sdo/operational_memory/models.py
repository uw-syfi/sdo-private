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


#: Where the health judge stores traffic mixes, one ``<name>.yaml`` per mix.
TRAFFIC_MIX_DIRECTORY = ".sdo/diagnostics/traffic"
#: Mirrors ``controller/sdk/traffic``: the Go runtime is the executor, this
#: model is the commit-time gate, and both must accept the same documents.
TRAFFIC_MAX_RATE_PER_SECOND = 20.0
TRAFFIC_MAX_TIMEOUT_SECONDS = 30.0
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


class TrafficTarget(TrafficModel):
    service: str = Field(pattern=_DNS_LABEL)
    port: int = Field(ge=1, le=65535)
    scheme: Literal["http", "https"] = "http"


class TrafficRequest(TrafficModel):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "://" in value
            or any(character in value for character in "?# \t\r\n")
        ):
            raise ValueError(f"path {value!r} must be an absolute path without a host, query, or fragment")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(name.lower() == "host" for name in value):
            raise ValueError("headers may not override Host")
        return value

    def mentions(self, marker: str) -> bool:
        return marker in self.body or any(
            marker in key or marker in value for values in (self.query, self.headers) for key, value in values.items()
        )


class TrafficExpect(TrafficModel):
    status: list[int] = Field(default_factory=list)
    body_contains: str = Field(default="", alias="bodyContains")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: list[int]) -> list[int]:
        for status in value:
            if not 100 <= status <= 599:
                raise ValueError(f"expected status {status} is not an HTTP status")
        return value


class TrafficSLO(TrafficModel):
    """Per-route service-level objective; unset fields inherit the mix, then defaults."""

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


#: Defaults applied by ``controller/sdk/traffic`` when a mix leaves them unset.
TRAFFIC_DEFAULT_SLO = TrafficSLO(
    window=5,
    min_samples=3,
    max_age="30s",
    max_error_rate=0.5,
    max_timeout_rate=0.5,
    latency_percentile=90,
    max_latency="1500ms",
)


def _merge_slo(base: TrafficSLO, override: TrafficSLO | None) -> TrafficSLO:
    if override is None:
        return base
    return base.model_copy(update=override.model_dump(exclude_none=True))


class TrafficRoute(TrafficRequest):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    weight: int = Field(default=1, ge=1, le=100)
    timeout: str | None = None
    expect: TrafficExpect = Field(default_factory=TrafficExpect)
    mutates: bool = False
    data_policy: Literal["idempotent", "self-cleaning"] | None = Field(default=None, alias="dataPolicy")
    synthetic_marker: str | None = Field(default=None, alias="syntheticMarker")
    cleanup: TrafficRequest | None = None
    slo: TrafficSLO | None = None

    @model_validator(mode="after")
    def validate_data_policy(self) -> TrafficRoute:
        if self.timeout is not None and not 0 < go_duration_seconds(self.timeout) <= TRAFFIC_MAX_TIMEOUT_SECONDS:
            raise ValueError(f"route {self.id!r}: timeout must be in (0, 30s]")
        if not self.mutates:
            if self.data_policy is not None or self.synthetic_marker is not None or self.cleanup is not None:
                raise ValueError(
                    f"route {self.id!r}: dataPolicy, syntheticMarker, and cleanup apply only to mutating routes"
                )
            return self
        if self.data_policy is None:
            raise ValueError(f"route {self.id!r}: a mutating route needs dataPolicy idempotent or self-cleaning")
        if not self.synthetic_marker or not self.synthetic_marker.strip():
            raise ValueError(f"route {self.id!r}: a mutating route needs a syntheticMarker naming its synthetic data")
        if not self.mentions(self.synthetic_marker):
            raise ValueError(
                f"route {self.id!r}: syntheticMarker {self.synthetic_marker!r} must appear in the route's query, "
                "body, or headers"
            )
        if self.data_policy == "self-cleaning":
            if self.cleanup is None:
                raise ValueError(f"route {self.id!r}: a self-cleaning route needs a cleanup request")
            if not self.cleanup.mentions(self.synthetic_marker):
                raise ValueError(f"route {self.id!r}: cleanup must target the syntheticMarker")
        elif self.cleanup is not None:
            raise ValueError(f"route {self.id!r}: cleanup applies only to dataPolicy self-cleaning")
        return self


class TrafficMix(TrafficModel):
    """Health-judge-owned synthetic traffic for one served entrypoint.

    Stored at ``.sdo/diagnostics/traffic/<name>.yaml`` and executed by the
    controller runtime; see ``controller/sdk/traffic`` for the semantics.
    """

    api_version: Literal["sdo.dev/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["TrafficMix"]
    name: str = Field(pattern=_DNS_LABEL)
    description: str = ""
    target: TrafficTarget
    rate_per_second: float = Field(default=4.0, gt=0, le=TRAFFIC_MAX_RATE_PER_SECOND, alias="ratePerSecond")
    timeout: str = "2s"
    slo: TrafficSLO = Field(default_factory=TrafficSLO)
    routes: list[TrafficRoute] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_routes(self) -> TrafficMix:
        if not 0 < go_duration_seconds(self.timeout) <= TRAFFIC_MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout must be in (0, 30s]")
        ids = [route.id for route in self.routes]
        duplicates = sorted({route_id for route_id in ids if ids.count(route_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate route id(s): {', '.join(duplicates)}")
        if all(route.mutates for route in self.routes):
            raise ValueError("at least one read route (mutates: false) is required")
        for route in self.routes:
            slo = self.route_slo(route)
            if slo.min_samples is not None and slo.window is not None and slo.min_samples > slo.window:
                raise ValueError(f"route {route.id!r}: slo minSamples must not exceed window")
        return self

    def route_slo(self, route: TrafficRoute) -> TrafficSLO:
        return _merge_slo(_merge_slo(TRAFFIC_DEFAULT_SLO, self.slo), route.slo)


class OutcomeClassification(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
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
