"""Benchmark-only gate that injects a deferred SREGym fault into a monitored app.

SDO operates an application continuously: it deploys, judges health, and
installs its controller before anything goes wrong. The gate reproduces that
order inside SREGym by asking the conductor to inject the fault only after the
controller has evaluated its detectors once with no active finding.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from benchmarks.sregym.protocol import request_with_retry

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable

INJECT_FAULT_ENDPOINT = "/inject_fault"
AWAITING_FAULT_INJECTION = "awaiting_fault_injection"
BASELINE_TIMEOUT_SECONDS = 1800
BASELINE_POLL_SECONDS = 1.0
PERSISTENT_LOG_TAIL = 2000
PROBER_POD = "pod/sdo-prober"
#: A link-reachability finding needs the edge to have connected once, so the fault must not land before the
#: prober has been dialling for a while. Dials run every second, so this leaves many samples.
PROBER_WARMUP_SECONDS = 30.0
#: An application without traffic workloads has no prober; do not wait for one longer than this.
PROBER_APPEAR_SECONDS = 45.0
PROBER_WAIT_CAP_SECONDS = 300.0


class FaultGateError(RuntimeError):
    """Raised when the fault cannot be injected into a monitored baseline."""


class KubectlRunner(Protocol):
    def __call__(self, args: list[str], *, namespace: str, check: bool) -> subprocess.CompletedProcess[str]: ...


def controller_active_findings(controller_logs: str) -> list[str] | None:
    """Return the active rule IDs of the latest controller evaluation, or None before any evaluation."""

    latest: list[str] | None = None
    for line in controller_logs.splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "controller_iteration" not in payload:
            continue
        findings = payload.get("findings") or []
        latest = [
            str(finding.get("rule_id"))
            for finding in (findings if isinstance(findings, list) else [])
            if isinstance(finding, dict) and finding.get("status") == "active"
        ]
    return latest


def controller_baseline_clear(controller_logs: str) -> bool:
    """Return whether the latest controller evaluation reported no active finding."""

    return controller_active_findings(controller_logs) == []


def request_fault_injection(api_base: str) -> None:
    """Ask the conductor to inject the deferred fault and open the incident."""

    response = request_with_retry("POST", f"{api_base.rstrip('/')}{INJECT_FAULT_ENDPOINT}", max_retries=2, timeout=300)
    stage = response.json().get("stage")
    if stage in (None, AWAITING_FAULT_INJECTION):
        raise FaultGateError(f"conductor did not open an incident stage after injection (stage={stage!r})")


def inject_fault_after_controller_baseline(
    namespace: str,
    *,
    inject: Callable[[], None],
    kubectl_runner: KubectlRunner,
    not_before: datetime,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = BASELINE_TIMEOUT_SECONDS,
) -> dict[str, float]:
    """Wait for an all-clear evaluation from a controller Job created at or after
    ``not_before`` (never a previous round's Job), then inject exactly once."""

    started = monotonic()
    deadline = started + timeout_seconds
    last_error = "controller Job has not produced an evaluation"
    while monotonic() < deadline:
        if not _current_controller_job(namespace, kubectl_runner, not_before):
            last_error = "current controller Job has not been created"
            sleep(BASELINE_POLL_SECONDS)
            continue
        completed = kubectl_runner(["logs", "job/sdo-controller-run", "--tail=200"], namespace=namespace, check=False)
        active = controller_active_findings(completed.stdout) if completed.returncode == 0 else None
        if active:
            last_error = f"latest controller evaluation has active findings {sorted(set(active))}"
        if active == []:
            baseline_ready = monotonic()
            inject()
            return {
                "controller_baseline_wait": baseline_ready - started,
                "fault_injection_request": monotonic() - baseline_ready,
            }
        if completed.returncode != 0:
            last_error = (completed.stderr or completed.stdout or "").strip() or last_error
        sleep(BASELINE_POLL_SECONDS)
    raise FaultGateError(f"controller did not report an all-clear baseline within {timeout_seconds:.0f}s: {last_error}")


def _current_controller_job(namespace: str, kubectl_runner: KubectlRunner, not_before: datetime) -> bool:
    completed = kubectl_runner(["get", "job/sdo-controller-run", "-o", "json"], namespace=namespace, check=False)
    if completed.returncode != 0:
        return False
    try:
        created = json.loads(completed.stdout)["metadata"]["creationTimestamp"]
        created_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    # creationTimestamp has one-second resolution.
    return created_at >= not_before.replace(microsecond=0)


def controller_active_findings_after_resume(controller_logs: str, generation: str) -> list[str] | None:
    """Return the latest evaluation's active rule IDs after the controller resumed ``generation``.

    A persistent controller observes several benchmark problems. Only an
    evaluation after it announced this stage's resume (following the stage's
    application deploy) counts; a later pause invalidates the baseline.
    """

    latest: list[str] | None = None
    resumed = False
    for line in controller_logs.splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "controller_maintenance" in payload:
            resumed = (
                payload.get("controller_maintenance") == "active"
                and payload.get("maintenance_generation") == generation
            )
            latest = None
            continue
        if resumed and "controller_iteration" in payload:
            latest = controller_active_findings(line)
    return latest


def inject_fault_after_resumed_baseline(
    control_namespace: str,
    generation: str,
    *,
    inject: Callable[[], None],
    kubectl_runner: KubectlRunner,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = BASELINE_TIMEOUT_SECONDS,
) -> dict[str, float]:
    """Inject once the persistent controller reports all-clear after resuming ``generation``.

    The controller's first all-clear can precede the prober's first dial, and a link detector reports only edges that
    connected at least once, so the gate also waits for a Ready prober to warm up before the fault lands.
    """

    started = monotonic()
    deadline = started + timeout_seconds
    last_error = f"controller has not resumed maintenance generation {generation!r}"
    while monotonic() < deadline:
        completed = kubectl_runner(
            ["logs", "job/sdo-controller-run", "--container=controller", f"--tail={PERSISTENT_LOG_TAIL}"],
            namespace=control_namespace,
            check=False,
        )
        active = (
            controller_active_findings_after_resume(completed.stdout, generation) if completed.returncode == 0 else None
        )
        if active:
            last_error = f"latest controller evaluation has active findings {sorted(set(active))}"
        if active == []:
            baseline_ready = monotonic()
            warm_wait = _wait_for_prober_warm(control_namespace, kubectl_runner, monotonic, sleep)
            warm_ready = monotonic()
            inject()
            return {
                "controller_baseline_wait": baseline_ready - started,
                "prober_warm_wait": warm_wait,
                "fault_injection_request": monotonic() - warm_ready,
            }
        if completed.returncode != 0:
            last_error = (completed.stderr or completed.stdout or "").strip() or last_error
        sleep(BASELINE_POLL_SECONDS)
    raise FaultGateError(f"controller did not report an all-clear baseline within {timeout_seconds:.0f}s: {last_error}")


def _prober_ready(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode != 0:
        return False
    try:
        pod = json.loads(completed.stdout)
        status = pod["status"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False
    if not isinstance(status, dict) or status.get("phase") != "Running":
        return False
    conditions = status.get("conditions") or []
    return any(
        isinstance(condition, dict) and condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )


def _wait_for_prober_warm(
    control_namespace: str,
    kubectl_runner: KubectlRunner,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> float:
    """Wait until the controller's prober pod has been Ready for PROBER_WARMUP_SECONDS; return the seconds waited.

    Returns early when no prober pod shows up (an application without traffic workloads) and gives up at a cap.
    """

    started = monotonic()
    ready_since: float | None = None
    seen = False
    while True:
        now = monotonic()
        completed = kubectl_runner(["get", PROBER_POD, "-o", "json"], namespace=control_namespace, check=False)
        if _prober_ready(completed):
            seen = True
            ready_since = now if ready_since is None else ready_since
            if now - ready_since >= PROBER_WARMUP_SECONDS:
                return now - started
        else:
            ready_since = None
            seen = seen or completed.returncode == 0
        if now - started >= PROBER_WAIT_CAP_SECONDS or (not seen and now - started >= PROBER_APPEAR_SECONDS):
            return now - started
        sleep(BASELINE_POLL_SECONDS)
