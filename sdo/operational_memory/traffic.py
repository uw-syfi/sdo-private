"""Host loader and validator for the proto-defined traffic workload schema.

The schema -- the fields and their types -- is the generated proto
``TrafficWorkload`` tree re-exported from :mod:`sdo.contracts.proto`, sourced
from ``proto/sdodev/contracts/v1alpha1/traffic.proto``. This module is the
commit-time validation gate for ``.sdo/diagnostics/traffic/workloads/
<name>.yaml``.

The repo keeps no Python protovalidate/CEL runtime (see ``proto/README.md``),
so the invariants the proto expresses as protovalidate CEL -- checked on the Go
side -- are mirrored here as behavior-preserving host code. Go-duration bounds
and the cross-field checks that depend on one cannot be expressed in CEL on
either side and are host code on both loaders. Unset defaulted scalars carry a
zero/empty sentinel (matching ``controller/sdk/traffic`` ``WithDefaults``); this
module fills them before it checks the bounds.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from google.protobuf import json_format

from sdo.contracts.proto import TrafficLink, TrafficSLO, TrafficWorkload, TrafficWorkloadScenario

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "TRAFFIC_LINK_DEFAULT_FAILURES",
    "TRAFFIC_LINK_MAX_LINKS",
    "TRAFFIC_MAX_BURST_SECONDS",
    "TRAFFIC_MAX_ITERATION_TIMEOUT_SECONDS",
    "TRAFFIC_MAX_RATE_PER_SECOND",
    "TRAFFIC_MAX_TIMEOUT_SECONDS",
    "TrafficLink",
    "TrafficSLO",
    "TrafficWorkload",
    "TrafficWorkloadError",
    "TrafficWorkloadScenario",
    "go_duration_seconds",
    "load_traffic_workload",
    "scenario_slo",
]

TRAFFIC_MAX_RATE_PER_SECOND: Final = 20.0
TRAFFIC_MAX_TIMEOUT_SECONDS: Final = 10.0
TRAFFIC_MAX_ITERATION_TIMEOUT_SECONDS: Final = 30.0
TRAFFIC_MAX_BURST_SECONDS: Final = 60.0
TRAFFIC_LINK_DEFAULT_FAILURES: Final = 5
TRAFFIC_LINK_MAX_LINKS: Final = 64
_TRAFFIC_LINK_MIN_FAILURES: Final = 2
_TRAFFIC_LINK_MAX_FAILURES: Final = 60
_TRAFFIC_LINK_MIN_INTERVAL_SECONDS: Final = 0.1
_TRAFFIC_LINK_MAX_INTERVAL_SECONDS: Final = 60.0
_TRAFFIC_SLO_MAX_AGE_SECONDS: Final = 600.0

_API_VERSION: Final = "sdo.dev/v1alpha1"
_KIND: Final = "TrafficWorkload"
_DEFAULT_RATE_PER_SECOND: Final = 4.0
_DEFAULT_TIMEOUT: Final = "2s"
_DEFAULT_ITERATION_TIMEOUT: Final = "10s"

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_GO_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_GO_DURATION_SECONDS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}

#: Defaults the controller applies (``controller/sdk/traffic`` ``defaultSLO``).
_DEFAULT_SLO_FIELDS: Final = {
    "window": 5,
    "min_samples": 3,
    "max_age": "30s",
    "max_error_rate": 0.5,
    "max_timeout_rate": 0.5,
    "latency_percentile": 90,
    "max_latency": "1500ms",
}


class TrafficWorkloadError(ValueError):
    """A traffic workload artifact the commit-time validation gate rejects."""


def go_duration_seconds(value: str) -> float:
    """Seconds in a Go duration string such as ``1m30s`` or ``500ms``."""

    parts = list(_GO_DURATION_PART.finditer(value))
    if not parts or "".join(part.group(0) for part in parts) != value:
        raise TrafficWorkloadError(f"{value!r} is not a Go duration such as 2s or 500ms")
    return sum(float(part.group(1)) * _GO_DURATION_SECONDS[part.group(2)] for part in parts)


def load_traffic_workload(document: Mapping[str, object] | None) -> TrafficWorkload:
    """Parse and validate one workload document into the proto ``TrafficWorkload``.

    ``document`` is a parsed YAML mapping. Unknown fields are rejected (the proto
    schema is the field set); then every invariant the Pydantic model enforced is
    checked in host code so the gate rejects exactly what it rejected before.
    """

    workload = TrafficWorkload()
    try:
        json_format.ParseDict(document or {}, workload)
    except json_format.ParseError as exc:
        raise TrafficWorkloadError(str(exc)) from exc
    _validate_workload(workload)
    return workload


def _validate_duration(value: str, *, field: str) -> float:
    seconds = go_duration_seconds(value)
    if seconds <= 0:
        raise TrafficWorkloadError(f"{field} duration must be positive")
    return seconds


def _validate_slo(slo: TrafficSLO) -> None:
    """Check the bounds of every present SLO field (CEL on the Go side)."""

    if slo.HasField("window") and not 1 <= slo.window <= 100:
        raise TrafficWorkloadError("slo window must be in [1, 100] samples")
    if slo.HasField("min_samples") and slo.min_samples < 1:
        raise TrafficWorkloadError("slo minSamples must be at least 1")
    if slo.HasField("max_error_rate") and not 0 < slo.max_error_rate <= 1:
        raise TrafficWorkloadError("slo maxErrorRate must be in (0, 1]")
    if slo.HasField("max_timeout_rate") and not 0 < slo.max_timeout_rate <= 1:
        raise TrafficWorkloadError("slo maxTimeoutRate must be in (0, 1]")
    if slo.HasField("latency_percentile") and not 1 <= slo.latency_percentile <= 100:
        raise TrafficWorkloadError("slo latencyPercentile must be in [1, 100]")
    for field in ("max_age", "max_latency"):
        if slo.HasField(field):
            _validate_duration(getattr(slo, field), field=field)


def _validate_workload(workload: TrafficWorkload) -> None:
    if workload.api_version != _API_VERSION:
        raise TrafficWorkloadError(f"apiVersion must be {_API_VERSION}")
    if workload.kind != _KIND:
        raise TrafficWorkloadError(f"kind must be {_KIND}")
    if not _DNS_LABEL.match(workload.name):
        raise TrafficWorkloadError(f"name {workload.name!r} must be a lowercase DNS label")
    if workload.purpose not in {"health-probe", "verify-burst", "journey", "link-probe"}:
        raise TrafficWorkloadError(f"purpose {workload.purpose!r} is not a known traffic purpose")
    if workload.arrival not in {"", "uniform", "poisson"}:
        raise TrafficWorkloadError("arrival must be uniform or poisson")
    rate = workload.rate_per_second or _DEFAULT_RATE_PER_SECOND
    if not 0 < rate <= TRAFFIC_MAX_RATE_PER_SECOND:
        raise TrafficWorkloadError(f"ratePerSecond must be in (0, {TRAFFIC_MAX_RATE_PER_SECOND:g}]")
    _validate_slo(workload.slo)

    if workload.purpose == "link-probe":
        _validate_link_probe(workload)
        return

    if workload.links or workload.HasField("interval") or workload.HasField("failures"):
        raise TrafficWorkloadError("links, interval and failures apply only to a link-probe workload")
    if not workload.scenarios:
        raise TrafficWorkloadError("at least one scenario is required")
    if workload.purpose == "health-probe":
        if workload.HasField("duration"):
            raise TrafficWorkloadError("a health-probe runs continuously and takes no duration")
    else:
        burst = go_duration_seconds(workload.duration) if workload.HasField("duration") else 0.0
        if not 0 < burst <= TRAFFIC_MAX_BURST_SECONDS:
            raise TrafficWorkloadError(f"a {workload.purpose} workload needs a duration in (0, 60s]")

    timeout = _validate_duration(workload.timeout or _DEFAULT_TIMEOUT, field="timeout")
    if not 0 < timeout <= TRAFFIC_MAX_TIMEOUT_SECONDS:
        raise TrafficWorkloadError("timeout must be in (0, 10s]")
    iteration = _validate_duration(workload.iteration_timeout or _DEFAULT_ITERATION_TIMEOUT, field="iterationTimeout")
    if not timeout <= iteration <= TRAFFIC_MAX_ITERATION_TIMEOUT_SECONDS:
        raise TrafficWorkloadError("iterationTimeout must be in [timeout, 30s]")

    ids = [scenario.id for scenario in workload.scenarios]
    for scenario in workload.scenarios:
        if not _SCENARIO_ID.match(scenario.id):
            raise TrafficWorkloadError(f"scenario id {scenario.id!r} is invalid")
        if scenario.weight and not 1 <= scenario.weight <= 100:
            raise TrafficWorkloadError(f"scenario {scenario.id!r} weight must be in [1, 100]")
        if scenario.HasField("slo"):
            _validate_slo(scenario.slo)
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicates:
        raise TrafficWorkloadError(f"scenario(s) listed twice: {', '.join(duplicates)}")
    for scenario in workload.scenarios:
        slo = scenario_slo(workload, scenario.id)
        if slo.HasField("min_samples") and slo.HasField("window") and slo.min_samples > slo.window:
            raise TrafficWorkloadError(f"scenario {scenario.id!r}: slo minSamples must not exceed window")


def _validate_link_probe(workload: TrafficWorkload) -> None:
    if workload.HasField("duration"):
        raise TrafficWorkloadError("a link-probe runs continuously and takes no duration")
    if workload.scenarios:
        raise TrafficWorkloadError("a link-probe runs no scenarios")
    if not 0 < len(workload.links) <= TRAFFIC_LINK_MAX_LINKS:
        raise TrafficWorkloadError(f"a link-probe needs 1 to {TRAFFIC_LINK_MAX_LINKS} links")
    edges: list[tuple[str, str, int]] = []
    for link in workload.links:
        source, target = getattr(link, "from"), link.to
        if not _DNS_LABEL.match(source) or not _DNS_LABEL.match(target):
            raise TrafficWorkloadError(f"link {source} -> {target} must name Services")
        if source == target:
            raise TrafficWorkloadError(f"link {source!r} calls itself")
        if not 1 <= link.port <= 65535:
            raise TrafficWorkloadError(f"link {source} -> {target} port {link.port} is out of range")
        if link.protocol not in {"", "tcp"}:
            raise TrafficWorkloadError(f"link {source} -> {target} protocol must be tcp")
        edges.append((source, target, link.port))
    duplicates = sorted({f"{a} -> {b}:{c}" for a, b, c in edges if edges.count((a, b, c)) > 1})
    if duplicates:
        raise TrafficWorkloadError(f"link(s) listed twice: {', '.join(duplicates)}")
    if workload.HasField("interval"):
        interval = go_duration_seconds(workload.interval)
        if not _TRAFFIC_LINK_MIN_INTERVAL_SECONDS <= interval <= _TRAFFIC_LINK_MAX_INTERVAL_SECONDS:
            raise TrafficWorkloadError("interval must be in [100ms, 1m]")
    if workload.HasField("failures") and not (
        _TRAFFIC_LINK_MIN_FAILURES <= workload.failures <= _TRAFFIC_LINK_MAX_FAILURES
    ):
        raise TrafficWorkloadError(f"failures must be in [{_TRAFFIC_LINK_MIN_FAILURES}, {_TRAFFIC_LINK_MAX_FAILURES}]")
    timeout = _validate_duration(workload.timeout or "1s", field="timeout")
    if not 0 < timeout <= TRAFFIC_MAX_TIMEOUT_SECONDS:
        raise TrafficWorkloadError("timeout must be in (0, 10s]")


def _merge_slo(base: TrafficSLO, override: TrafficSLO) -> TrafficSLO:
    merged = TrafficSLO()
    merged.CopyFrom(base)
    for field in _DEFAULT_SLO_FIELDS:
        if override.HasField(field):
            setattr(merged, field, getattr(override, field))
    return merged


def scenario_slo(workload: TrafficWorkload, scenario_id: str) -> TrafficSLO:
    """Effective SLO of ``scenario_id``: defaults, then the workload SLO, then the scenario override."""

    slo = TrafficSLO(**_DEFAULT_SLO_FIELDS)
    if workload.HasField("slo"):
        slo = _merge_slo(slo, workload.slo)
    for scenario in workload.scenarios:
        if scenario.id == scenario_id and scenario.HasField("slo"):
            slo = _merge_slo(slo, scenario.slo)
    return slo
