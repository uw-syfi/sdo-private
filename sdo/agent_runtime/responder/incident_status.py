"""``sdo incident status``: the responder's verify-before-submit check.

The controller closes an incident only after its health detectors clear, and
the synthetic-traffic detectors clear only when the health judge's scenarios
meet their SLOs again. This command runs the judge's verify-burst workload
through the same isolated prober, now, so the responder can confirm the
repair against the acceptance test the controller will apply instead of
guessing from rollout state. The controller passes the prober's address as
``SDO_PROBER_URL``; without it the command reports ``unavailable`` and the
responder falls back to its own verification.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sdo.contracts import FindingStatus, IncidentRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

PROBER_URL_ENVIRONMENT = "SDO_PROBER_URL"
REQUEST_PATH_ENVIRONMENT = "SDO_REQUEST_PATH"
DEFAULT_REQUEST_PATH = Path("/sdo/request/incident-request.json")
#: Rule-ID prefix of the synthetic-traffic health detector's findings.
SCENARIO_RULE_PREFIX = "scenario-slo."
BURST_PATH = "/v1/bursts"
#: A verify burst is capped at 60 seconds by the workload schema.
BURST_TIMEOUT_SECONDS = 90.0

EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1
EXIT_UNAVAILABLE = 3

_MAX_FAILURES_SHOWN = 3


class IncidentStatusState(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNAVAILABLE = "unavailable"


class VerifyScenarioVerdict(BaseModel):
    """One scenario's judgement after a verify burst, as the prober reports it."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    scenario: str
    healthy: bool
    qualified: bool = True
    evaluated: bool = False
    samples: int = 0
    error_rate: float = Field(default=0.0, alias="errorRate")
    latency_ms: int = Field(default=0, alias="latencyMs")
    violations: list[str] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict, alias="statusCounts")
    recent_failures: list[str] = Field(default_factory=list, alias="recentFailures")

    @property
    def blocking(self) -> bool:
        return self.qualified and not self.healthy


class VerifyBurstResult(BaseModel):
    """A verify burst's outcome; the prober's raw observation window is dropped."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    workload: str
    healthy: bool
    started_at: str = Field(default="", alias="startedAt")
    duration_ns: int = Field(default=0, alias="durationNs")
    verdicts: list[VerifyScenarioVerdict] = Field(default_factory=list)


@dataclass(frozen=True)
class IncidentStatus:
    state: IncidentStatusState
    burst: VerifyBurstResult | None
    detail: str
    incident_scenarios: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.state, IncidentStatusState):
            raise TypeError("state must be a IncidentStatusState")
        if self.state != IncidentStatusState.UNAVAILABLE and self.burst is None:
            raise ValueError("a healthy or unhealthy status needs the verify burst that produced it")

    @property
    def exit_code(self) -> int:
        return {
            IncidentStatusState.HEALTHY: EXIT_HEALTHY,
            IncidentStatusState.UNHEALTHY: EXIT_UNHEALTHY,
            IncidentStatusState.UNAVAILABLE: EXIT_UNAVAILABLE,
        }[self.state]

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "incident_scenarios": list(self.incident_scenarios),
            "burst": None if self.burst is None else self.burst.model_dump(mode="json"),
        }

    def render(self) -> str:
        if self.burst is None:
            return (
                f"incident status: UNAVAILABLE ({self.detail}). No synthetic-traffic verification is available; "
                "rely on your own verification of the health objective."
            )
        seconds = self.burst.duration_ns / 1e9
        lines = [
            f"incident status: {self.state.value.upper()} "
            f"(verify burst {self.burst.workload!r}, {len(self.burst.verdicts)} scenario(s), {seconds:.1f}s)"
        ]
        incident = set(self.incident_scenarios)
        ordered = sorted(self.burst.verdicts, key=lambda verdict: (verdict.scenario not in incident, verdict.scenario))
        for verdict in ordered:
            label = verdict.scenario + (" (triggered this incident)" if verdict.scenario in incident else "")
            if verdict.healthy:
                lines.append(
                    f"  ok    {label}: {verdict.samples} samples, error rate {verdict.error_rate:.2f}, "
                    f"latency {verdict.latency_ms}ms"
                )
                continue
            tag = "FAIL " if verdict.qualified else "skip "
            text = f"  {tag} {label}: {verdict.samples} samples, error rate {verdict.error_rate:.2f}"
            if verdict.violations:
                text += f"; {'; '.join(verdict.violations)}"
            if verdict.status_counts:
                counts = ", ".join(f"{status} x{count}" for status, count in sorted(verdict.status_counts.items()))
                text += f"; statuses: {counts}"
            if not verdict.qualified:
                text += "; never passed since the controller started, so it does not block closure"
            lines.append(text)
            lines.extend(f"          {failure}" for failure in verdict.recent_failures[:_MAX_FAILURES_SHOWN])
        if self.state == IncidentStatusState.UNHEALTHY:
            lines.append(
                "The controller will not close this incident while these scenarios fail. Keep repairing, then run "
                "`sdo incident status` again before submitting mitigation or returning the result."
            )
        return "\n".join(lines)


Opener = Callable[..., Any]


def incident_status(
    *,
    prober_url: str | None,
    request_path: Path | None,
    workload: str | None = None,
    scenarios: Sequence[str] = (),
    timeout_seconds: float = BURST_TIMEOUT_SECONDS,
    opener: Opener = urllib.request.urlopen,
) -> IncidentStatus:
    """Run a verify burst now and judge it as the closure gate would."""

    incident = _incident_scenarios(request_path)
    if not prober_url:
        return IncidentStatus(
            state=IncidentStatusState.UNAVAILABLE,
            burst=None,
            detail=f"{PROBER_URL_ENVIRONMENT} is not set",
            incident_scenarios=incident,
        )
    body: dict[str, Any] = {}
    if workload:
        body["workload"] = workload
    if scenarios:
        body["scenarios"] = list(scenarios)
    request = urllib.request.Request(
        prober_url.rstrip("/") + BURST_PATH,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            burst = VerifyBurstResult.model_validate_json(response.read())
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace").strip() or str(exc)
        return IncidentStatus(
            state=IncidentStatusState.UNAVAILABLE,
            burst=None,
            detail=f"prober rejected the burst: {message}",
            incident_scenarios=incident,
        )
    except (OSError, ValidationError, ValueError) as exc:
        return IncidentStatus(
            state=IncidentStatusState.UNAVAILABLE,
            burst=None,
            detail=f"prober unreachable: {exc}",
            incident_scenarios=incident,
        )
    blocking = any(verdict.blocking for verdict in burst.verdicts)
    state = IncidentStatusState.UNHEALTHY if blocking or not burst.verdicts else IncidentStatusState.HEALTHY
    healthy = state == IncidentStatusState.HEALTHY
    detail = "every qualified scenario meets its SLO" if healthy else "scenarios violate their SLO"
    return IncidentStatus(state=state, burst=burst, detail=detail, incident_scenarios=incident)


def _incident_scenarios(request_path: Path | None) -> tuple[str, ...]:
    if request_path is None or not request_path.is_file():
        return ()
    try:
        request = IncidentRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(
        sorted(
            {
                finding.rule_id.removeprefix(SCENARIO_RULE_PREFIX)
                for finding in request.findings
                if finding.status == FindingStatus.ACTIVE and finding.rule_id.startswith(SCENARIO_RULE_PREFIX)
            }
        )
    )


def live_incident_status(*, workload: str | None = None, scenarios: Sequence[str] = ()) -> IncidentStatus:
    """The incident status for the responder's environment (prober URL and mounted request)."""

    return incident_status(
        prober_url=os.environ.get(PROBER_URL_ENVIRONMENT, "").strip() or None,
        request_path=Path(os.environ.get(REQUEST_PATH_ENVIRONMENT, str(DEFAULT_REQUEST_PATH))),
        workload=workload,
        scenarios=scenarios,
    )


def run_incident_status_cli(*, workload: str | None, scenarios: Sequence[str], as_json: bool) -> int:
    """Entry point for ``sdo incident status``."""

    status = live_incident_status(workload=workload, scenarios=scenarios)
    print(json.dumps(status.to_json(), indent=2) if as_json else status.render())
    return status.exit_code
