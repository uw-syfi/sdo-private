"""The assurance suite's JSON results: one record per fault run, plus soak and run summaries."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from pathlib import Path  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RESULTS_SCHEMA = "sdo.no-llm-assurance/v1"


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str = ""
    #: A known gap is reported but does not fail the run.
    known_gap: bool = False


class GateProbe(BaseModel):
    """One ``sdo incident status`` and submission-gate reading."""

    model_config = ConfigDict(extra="forbid")

    label: str
    at: datetime
    status_exit: int
    status_seconds: float
    gate_exit: int | None = None
    unhealthy_scenarios: list[str] = Field(default_factory=list)
    #: Non-traffic health detectors the controller still reports firing.
    blocking_detectors: list[str] = Field(default_factory=list)
    #: Objects the controller sees changed that the request's diff lacks.
    new_state_changes: list[str] = Field(default_factory=list)
    controller_detail: str = ""


class DiffReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected: list[str]
    named: list[str]
    missing: list[str]
    unexpected: list[str]
    decoys_named: list[str]
    baseline_at: datetime | None = None
    observed_at: datetime | None = None


class WrongFixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    applied: bool
    detail: str = ""
    probe: GateProbe


class PartialFixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_problem: str
    remaining_problems: list[str]
    probe: GateProbe


class HelperResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: list[str]
    cleaned_by_controller: list[str]
    remaining: list[str]
    bystander_kept: bool
    #: From the responder's exit until the last labelled helper was gone.
    cleanup_seconds: float | None = None


class AbsorptionResult(BaseModel):
    """The fault stayed longer than the baseline settle period, then came back right after closure."""

    model_config = ConfigDict(extra="forbid")

    held_seconds: float
    reinjected_diff: DiffReading
    reinjected_incident_id: str


class FaultRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: str
    kind: Literal["single", "composite"]
    iteration: int
    problems: list[str]
    started_at: datetime
    injection_started_at: datetime | None = None
    injection_finished_at: datetime | None = None
    incident_id: str | None = None
    #: Seconds from the end of injection; negative when a detector saw the outage while injection was still running.
    first_finding_seconds: dict[str, float] = Field(default_factory=dict)
    incident_opened_seconds: float | None = None
    request_received_seconds: float | None = None
    diff: DiffReading | None = None
    #: Faulted objects the request's diff lacked that the controller's live view reported.
    live_named: list[str] = Field(default_factory=list)
    on_fault: GateProbe | None = None
    wrong_fixes: list[WrongFixResult] = Field(default_factory=list)
    partial_fixes: list[PartialFixResult] = Field(default_factory=list)
    correct_fix_seconds: float | None = None
    #: From the correct fix's end until ``sdo incident status`` first exits 0.
    clear_seconds: float | None = None
    after_fix: GateProbe | None = None
    #: From the correct fix's end until the controller verified recovery.
    verified_seconds: float | None = None
    closure_acknowledged: bool = False
    final_detector_states: dict[str, str] = Field(default_factory=dict)
    diagnosis_verdicts: list[str] = Field(default_factory=list)
    helpers: HelperResult | None = None
    absorption: AbsorptionResult | None = None
    checks: list[Check] = Field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed or check.known_gap for check in self.checks)


class ResourceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: datetime
    controller_cpu_seconds: float
    controller_rss_bytes: int
    prober_cpu_seconds: float | None = None
    prober_working_set_bytes: int | None = None
    apiserver_requests: int | None = None


class SoakResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    finished_at: datetime
    evaluations: int
    evaluations_with_findings: int
    findings: list[str] = Field(default_factory=list)
    incidents_opened: int
    status_probes: int
    status_unhealthy: int
    samples: list[ResourceSample] = Field(default_factory=list)
    controller_cpu_millicores: float | None = None
    controller_rss_mib_max: float | None = None
    prober_cpu_millicores: float | None = None
    prober_working_set_mib_max: float | None = None
    controller_api_requests_per_second: float | None = None
    port_forward_restarts: int = 0
    checks: list[Check] = Field(default_factory=list)


class SuiteResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = RESULTS_SCHEMA
    run_id: str
    cluster: str
    seed_commit: str
    controller_binary_sha256: str
    prober_digest: str
    started_at: datetime
    finished_at: datetime | None = None
    runs: list[FaultRun] = Field(default_factory=list)
    soak: SoakResult | None = None
    port_forward_restarts: int = 0
    notes: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path
