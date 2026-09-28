"""The healthy false-alarm soak: traffic runs, nothing is injected, and nothing may fire."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.sregym.fastloop.assurance.harness import ControllerProcess, Kubectl, PortForward, utcnow
from benchmarks.sregym.fastloop.assurance.results import Check, ResourceSample, SoakResult

if TYPE_CHECKING:
    from benchmarks.sregym.fastloop.assurance.suite import AssuranceSuite

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
#: Requests from members of system:masters (this kubeconfig), counted by API Priority and Fairness.
_EXEMPT_REQUESTS = re.compile(
    r'^apiserver_flowcontrol_dispatched_requests_total\{[^}]*flow_schema="exempt"[^}]*\} (\S+)$', re.MULTILINE
)


def process_usage(pid: int) -> tuple[float, int]:
    """CPU seconds (user + system, children included) and resident bytes of a host process."""

    # Fields 14-17 of /proc/<pid>/stat: utime, stime, cutime, cstime (the text after the command name).
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    cpu = sum(int(value) for value in fields[11:15]) / CLOCK_TICKS
    rss = 0
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) * 1024
    return cpu, rss


def prober_usage(node: str) -> tuple[float | None, int | None]:
    """The prober container's CPU seconds and working set, read from the kind node's CRI."""

    completed = subprocess.run(
        ["docker", "exec", node, "crictl", "stats", "--label", "io.kubernetes.pod.name=sdo-prober", "--output", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return None, None
    stats = json.loads(completed.stdout).get("stats") or []
    if not stats:
        return None, None
    entry = stats[0]
    cpu = int(entry.get("cpu", {}).get("usageCoreNanoSeconds", {}).get("value", 0)) / 1e9
    memory = int(entry.get("memory", {}).get("workingSetBytes", {}).get("value", 0))
    return cpu, memory


def exempt_requests(kubectl: Kubectl) -> int | None:
    completed = kubectl.run("get", "--raw", "/metrics", check=False, timeout=60)
    if completed.returncode != 0:
        return None
    return int(sum(float(value) for value in _EXEMPT_REQUESTS.findall(completed.stdout)))


def run_soak(
    suite: AssuranceSuite,
    controller: ControllerProcess,
    kubectl: Kubectl,
    port_forward: PortForward,
    *,
    node: str,
    minutes: float,
    sample_seconds: float = 60.0,
    status_every_seconds: float = 300.0,
) -> SoakResult:
    if minutes <= 0:
        raise ValueError("the soak needs a positive duration")
    suite.wait_settled()
    started = utcnow()
    known = suite.known_requests()
    restarts_before = port_forward.restarts
    samples: list[ResourceSample] = []
    status_probes = status_unhealthy = 0
    own_requests = 0
    deadline = started + timedelta(minutes=minutes)
    next_status = time.monotonic()
    while True:
        now = utcnow()
        cpu, rss = process_usage(controller.pid)
        prober_cpu, prober_memory = prober_usage(node)
        samples.append(
            ResourceSample(
                at=now,
                controller_cpu_seconds=cpu,
                controller_rss_bytes=rss,
                prober_cpu_seconds=prober_cpu,
                prober_working_set_bytes=prober_memory,
                apiserver_requests=exempt_requests(kubectl),
            )
        )
        own_requests += 1
        if now >= deadline:
            break
        if time.monotonic() >= next_status:
            exit_code = suite.status(None).exit_code
            status_probes += 1
            status_unhealthy += int(exit_code != 0)
            next_status = time.monotonic() + status_every_seconds
        time.sleep(max(0.0, min(sample_seconds, (deadline - utcnow()).total_seconds())))
    finished = utcnow()
    evaluations = [event for event in controller.events(started) if "controller_iteration" in event.record]
    noisy = [event for event in evaluations if event.record.get("findings")]
    findings = sorted(
        {
            f"{finding.get('detector_id')}:{finding.get('rule_id')}"
            for event in noisy
            for finding in event.record.get("findings") or []
        }
    )
    incidents = len(suite.known_requests() - known)
    first, last = samples[0], samples[-1]
    elapsed = (last.at - first.at).total_seconds() or 1.0
    result = SoakResult(
        started_at=started,
        finished_at=finished,
        evaluations=len(evaluations),
        evaluations_with_findings=len(noisy),
        findings=findings,
        incidents_opened=incidents,
        status_probes=status_probes,
        status_unhealthy=status_unhealthy,
        samples=samples,
        controller_cpu_millicores=round(
            1000 * (last.controller_cpu_seconds - first.controller_cpu_seconds) / elapsed, 2
        ),
        controller_rss_mib_max=round(max(sample.controller_rss_bytes for sample in samples) / 2**20, 1),
        port_forward_restarts=port_forward.restarts - restarts_before,
    )
    if first.prober_cpu_seconds is not None and last.prober_cpu_seconds is not None:
        result.prober_cpu_millicores = round(1000 * (last.prober_cpu_seconds - first.prober_cpu_seconds) / elapsed, 2)
        result.prober_working_set_mib_max = round(
            max(sample.prober_working_set_bytes or 0 for sample in samples) / 2**20, 1
        )
    if first.apiserver_requests is not None and last.apiserver_requests is not None:
        # The suite's own metrics scrapes are members of the same flow; subtract them.
        requests = last.apiserver_requests - first.apiserver_requests - (own_requests - 1)
        result.controller_api_requests_per_second = round(requests / elapsed, 3)
    result.checks = [
        Check(name="no detector finding while healthy", passed=not noisy, detail=f"{len(noisy)} of {len(evaluations)}"),
        Check(name="no incident opened while healthy", passed=incidents == 0, detail=f"{incidents} opened"),
        Check(
            name="incident status healthy throughout",
            passed=status_unhealthy == 0,
            detail=f"{status_unhealthy} of {status_probes}",
        ),
    ]
    return result
