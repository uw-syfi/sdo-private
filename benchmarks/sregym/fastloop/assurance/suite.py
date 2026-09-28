"""The no-LLM assurance suite: SDO's feedback loop against real faults on a warm kind lane.

For each fault (or composition of faults) the suite injects it with the
fast-loop fault driver and checks, against the production controller:

1. a health detector fires and the controller opens an incident within a bound;
2. the incident's healthy-state diff names every faulted object and no decoy;
3. ``sdo incident status`` exits 1 and the submission gate refuses (exit 4);
4. after each scripted wrong fix (and, for a composition, after fixing all
   but one fault) both still refuse;
5. after the correct fix, status returns to 0 within a bound and the gate
   accepts;
6. labelled responder helpers are deleted by the controller and an
   unlabelled bystander is kept;
7. the controller verifies recovery, closes the incident, and the outcome's
   diagnosis verification confirms the scripted root cause;
8. optionally, the fault is held past the baseline settle period and
   re-injected right after closure: the new diff must still name it, so the
   faulty state was never absorbed into the healthy baseline.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from benchmarks.sregym.fastloop.assurance.catalog import DECOYS, CompositeCase, FaultCase, WrongFix
from benchmarks.sregym.fastloop.assurance.harness import (
    HELPER_LABEL,
    STATE_CONFIGMAP,
    ControllerProcess,
    HarnessError,
    Kubectl,
    broker_ledger,
    utcnow,
)
from benchmarks.sregym.fastloop.assurance.responder import exited_path, request_path, result_path
from benchmarks.sregym.fastloop.assurance.results import (
    AbsorptionResult,
    Check,
    DiffReading,
    FaultRun,
    GateProbe,
    HelperResult,
    PartialFixResult,
    WrongFixResult,
)
from benchmarks.sregym.fastloop.codex_agent import SubmissionStub

if TYPE_CHECKING:
    from pathlib import Path

    from benchmarks.sregym.fastloop.fault_driver import SregymFaultDriver

logger = logging.getLogger(__name__)

#: ``controller/runtime.DefaultStateSettle``: a new state replaces the baseline after this long healthy.
STATE_SETTLE_SECONDS = 120.0
EXIT_HEALTHY, EXIT_UNHEALTHY, EXIT_GATE_REFUSED = 0, 1, 4


@dataclass
class StatusReading:
    """One ``sdo incident status --json`` run."""

    exit_code: int
    seconds: float
    unhealthy: list[str] = field(default_factory=list)
    blocking_detectors: list[str] = field(default_factory=list)
    new_state_changes: list[str] = field(default_factory=list)
    controller_detail: str = ""


@dataclass(frozen=True)
class Bounds:
    """Pass bounds, fixed before the runs (see NO_LLM_SUITE_DECISIONS.md)."""

    #: End of injection to the controller opening the incident.
    detect_seconds: float = 30.0
    #: End of the correct fix to the first healthy ``sdo incident status``.
    clear_seconds: float = 60.0
    #: End of the correct fix to the controller's verified closure.
    verify_seconds: float = 90.0
    #: Responder exit to every labelled helper being gone.
    cleanup_seconds: float = 15.0
    #: Margin past the settle period before a fault, so the baseline holds the current healthy state.
    settle_margin_seconds: float = 20.0

    def __post_init__(self) -> None:
        for name in ("detect_seconds", "clear_seconds", "verify_seconds", "cleanup_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def diff_reading(request: dict[str, Any], expected: tuple[str, ...], *, allowed: tuple[str, ...] = ()) -> DiffReading:
    """Compare an incident request's state changes with the objects the fault changed."""

    changes = request.get("state_changes") or {}
    named = sorted({f"{change['kind']}/{change['name']}" for change in changes.get("changes") or []})
    decoys = sorted(item for item in named if any(item.endswith(f"/{decoy}") for decoy in DECOYS))
    return DiffReading(
        expected=sorted(expected),
        named=named,
        missing=sorted(set(expected) - set(named)),
        unexpected=sorted(set(named) - set(expected) - set(allowed)),
        decoys_named=decoys,
        baseline_at=changes.get("baseline_at"),
        observed_at=changes.get("observed_at"),
    )


def first_findings(controller: ControllerProcess, *, since: datetime, reference: datetime) -> dict[str, float]:
    """Seconds from ``reference`` to each detector's first raw finding after ``since``."""

    first: dict[str, float] = {}
    for event in controller.events(since):
        for finding in event.record.get("findings") or []:
            detector = str(finding.get("detector_id") or "?")
            if detector not in first:
                first[detector] = round((event.at - reference).total_seconds(), 3)
    return first


def scripted_result(
    request: dict[str, Any],
    *,
    objects: tuple[str, ...],
    summary: str,
    actions: list[dict[str, Any]],
    started_at: datetime,
    verification: list[dict[str, Any]],
) -> dict[str, Any]:
    """An incident result a careful responder would write: live evidence for each cited object."""

    fired = sorted({str(finding["detector_id"]) for finding in request.get("findings") or []})
    # Only the request's own diff is state-change evidence; an object the diff lacks (a fault
    # that landed after dispatch) was seen live, through `sdo incident status` or kubectl.
    in_diff = set(diff_reading(request, objects).named)
    evidence = [
        {"kind": "state-change", "source": item, "observation": f"{item} differs from the healthy baseline"}
        if item in in_diff
        else {"kind": "live-observation", "source": item, "observation": f"{item} is faulty now"}
        for item in objects
    ] + [
        {"kind": "detector-finding", "source": detector, "observation": f"{detector} fired for this incident"}
        for detector in fired
    ]
    namespace = str(request.get("namespace", ""))
    resources = [
        {"kind": item.split("/", 1)[0], "namespace": namespace, "name": item.split("/", 1)[1]} for item in objects
    ]
    return {
        "schema_version": request["schema_version"],
        "incident_id": request["incident_id"],
        "status": "completed",
        "confirmed_root_causes": [
            {"summary": summary, "resources": resources, "evidence": evidence, "explained_detectors": fired}
        ],
        "applied_playbooks": [],
        "repair_changes": [],
        "repair_actions": actions,
        "final_detector_states": [],
        "proposed_memory_changes": [],
        "verification_evidence": verification,
        "usage": {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        "timing": {"started_at": started_at.isoformat(), "completed_at": utcnow().isoformat()},
        "responder_session_id": "",
    }


def _action(action_id: str, *, target: str, summary: str, started: datetime, success: bool) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "kind": "kubectl",
        "target": target,
        "summary": summary,
        "details": summary,
        "started_at": started.isoformat(),
        "completed_at": utcnow().isoformat(),
        "success": success,
        "reversible": True,
    }


class AssuranceSuite:
    def __init__(
        self,
        *,
        kubectl: Kubectl,
        driver: SregymFaultDriver,
        controller: ControllerProcess,
        namespace: str,
        control_namespace: str,
        app_root: Path,
        spool: Path,
        prober_url: str,
        helper_image: str,
        bounds: Bounds | None = None,
    ) -> None:
        self.kubectl = kubectl
        self.driver = driver
        self.controller = controller
        self.namespace = namespace
        self.control_namespace = control_namespace
        self.app_root = app_root
        self.spool = spool
        self.prober_url = prober_url
        self.helper_image = helper_image
        self.bounds = bounds or Bounds()
        self._quiet_since: datetime | None = None
        self._runs = 0

    # --- probes -----------------------------------------------------------------------------------

    def _env(self, request_file: Path | None) -> dict[str, str]:
        # The environment the controller gives a local responder: the prober and its state ConfigMap.
        env = {**os.environ, "SDO_PROBER_URL": self.prober_url, "KUBECONFIG": str(self.kubectl.kubeconfig)}
        env["SDO_CONTROLLER_STATE"] = f"{self.control_namespace}/{STATE_CONFIGMAP}"
        env["SDO_REQUEST_PATH"] = str(request_file) if request_file is not None else str(self.spool / "absent.json")
        return env

    def status(self, request_file: Path | None) -> StatusReading:
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "sdo", "incident", "status", "--json"],
            capture_output=True,
            text=True,
            env=self._env(request_file),
            timeout=120,
            check=False,
        )
        reading = StatusReading(exit_code=completed.returncode, seconds=round(time.monotonic() - started, 3))
        try:
            payload = json.loads(completed.stdout)
            burst = payload.get("burst") or {}
            reading.unhealthy = [
                str(verdict.get("scenario"))
                for verdict in burst.get("verdicts", [])
                if isinstance(verdict, dict) and verdict.get("healthy") is False
            ]
            controller = payload.get("controller") or {}
            reading.controller_detail = str(controller.get("detail", ""))
            reading.blocking_detectors = [str(item) for item in controller.get("blocking_detectors", [])]
            reading.new_state_changes = [
                f"{change.get('kind')}/{change.get('name')}"
                for change in controller.get("new_state_changes", [])
                if isinstance(change, dict)
            ]
        except (json.JSONDecodeError, AttributeError):
            pass
        return reading

    def gate(self, request_file: Path | None) -> int:
        """Submit a mitigation through the SREGym submission bridge against a grading-free stub."""

        with SubmissionStub(app_info={"app_name": "hotel", "namespace": self.namespace}) as stub:
            env = {**self._env(request_file), "SDO_SREGYM_API_BASE": stub.url}
            command = [sys.executable, "-m", "benchmarks.sregym.adapter.submission"]
            subprocess.run([*command, "diagnosis", "scripted"], env=env, capture_output=True, timeout=120, check=False)
            completed = subprocess.run(
                [*command, "mitigation", "scripted"], env=env, capture_output=True, text=True, timeout=400, check=False
            )
            return completed.returncode

    def probe(self, label: str, request_file: Path | None, *, gate: bool = True) -> GateProbe:
        at = utcnow()
        reading = self.status(request_file)
        return GateProbe(
            label=label,
            at=at,
            status_exit=reading.exit_code,
            status_seconds=reading.seconds,
            gate_exit=self.gate(request_file) if gate else None,
            unhealthy_scenarios=reading.unhealthy,
            blocking_detectors=reading.blocking_detectors,
            new_state_changes=reading.new_state_changes,
            controller_detail=reading.controller_detail,
        )

    def wait_live_diff(self, request_file: Path, missing: list[str], *, timeout: float) -> list[str]:
        """Faults the request's diff lacks, as the controller's live view reports them through status."""

        deadline = time.monotonic() + timeout
        while True:
            seen = self.status(request_file).new_state_changes
            if set(missing) <= set(seen) or time.monotonic() >= deadline:
                return sorted(set(missing) & set(seen))
            time.sleep(1.0)

    # --- controller -------------------------------------------------------------------------------

    def wait_settled(self) -> None:
        """Wait until the controller has been quiet past the settle period, so the baseline is current."""

        needed = timedelta(seconds=STATE_SETTLE_SECONDS + self.bounds.settle_margin_seconds)
        while True:
            last_noisy = None
            for event in self.controller.events():
                if event.record.get("findings"):
                    last_noisy = event.at
            quiet_since = max(filter(None, [last_noisy, self._quiet_since, self.controller.started_at]))
            remaining = (quiet_since + needed - utcnow()).total_seconds()
            if remaining <= 0:
                return
            if not self.controller.alive():
                raise HarnessError("controller exited while waiting for a settled baseline")
            time.sleep(min(remaining, 5.0))

    def known_requests(self) -> set[str]:
        return {path.name for path in self.spool.glob("*.request.json")}

    def wait_request(self, *, known: set[str], timeout: float) -> tuple[Path, dict[str, Any]]:
        """The first incident request dispatched that is not in ``known`` (taken before the injection)."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for path in sorted(self.spool.glob("*.request.json")):
                if path.name not in known:
                    return path, json.loads(path.read_text(encoding="utf-8"))
            if not self.controller.alive():
                raise HarnessError("controller exited before dispatching the incident")
            time.sleep(0.1)
        raise HarnessError(f"no incident was dispatched within {timeout:.0f}s")

    def wait_closure(self, incident_id: str, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ledger = broker_ledger(self.app_root, incident_id)
            if ledger is not None and ledger.get("acknowledged"):
                self._quiet_since = utcnow()
                return ledger
            if not self.controller.alive():
                raise HarnessError("controller exited before closing the incident")
            time.sleep(0.25)
        raise HarnessError(f"incident {incident_id} was not closed and acknowledged within {timeout:.0f}s")

    # --- faults -----------------------------------------------------------------------------------

    def apply_wrong_fix(self, fix: WrongFix) -> tuple[bool, str]:
        if fix.kind == "restart":
            self.kubectl.run("rollout", "restart", f"deployment/{fix.target}", namespace=self.namespace)
            rolled = self.kubectl.run(
                "rollout",
                "status",
                f"deployment/{fix.target}",
                "--timeout=90s",
                namespace=self.namespace,
                check=False,
                timeout=120,
            )
            return True, "rolled out" if rolled.returncode == 0 else f"rollout incomplete: {rolled.stderr.strip()}"
        outputs = []
        for service in ("geo", "rate"):
            completed = self.kubectl.run(
                "exec",
                f"deployment/mongodb-{service}",
                "--",
                "bash",
                f"/scripts/revoke-mitigate-admin-{service}-mongo.sh",
                namespace=self.namespace,
                check=False,
                timeout=120,
            )
            tail = (completed.stdout or completed.stderr).strip().splitlines()[-1:] or [""]
            outputs.append(f"mongodb-{service}: exit {completed.returncode} {tail[0][:80]}")
        return True, "; ".join(outputs)

    def create_helpers(self, tag: str) -> tuple[list[str], str]:
        labels = {HELPER_LABEL: "true", "sdo.dev/assurance": tag}

        def pod(name: str, namespace: str, pod_labels: dict[str, str]) -> dict[str, Any]:
            return {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": name, "namespace": namespace, "labels": pod_labels},
                "spec": {
                    "restartPolicy": "Never",
                    "terminationGracePeriodSeconds": 1,
                    "containers": [
                        {
                            "name": "helper",
                            "image": self.helper_image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/usr/bin/sleep", "3600"],
                        }
                    ],
                },
            }

        created = [
            f"Pod/{self.namespace}/sdo-assure-helper-{tag}",
            f"Pod/{self.control_namespace}/sdo-assure-helper-{tag}",
            f"Job/{self.namespace}/sdo-assure-helper-job-{tag}",
        ]
        self.kubectl.apply(pod(f"sdo-assure-helper-{tag}", self.namespace, labels))
        self.kubectl.apply(pod(f"sdo-assure-helper-{tag}", self.control_namespace, labels))
        job_pod = pod(f"sdo-assure-helper-job-{tag}", self.namespace, labels)
        self.kubectl.apply(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": f"sdo-assure-helper-job-{tag}", "namespace": self.namespace, "labels": labels},
                "spec": {
                    "backoffLimit": 0,
                    "template": {"metadata": {"labels": labels}, "spec": job_pod["spec"]},
                },
            }
        )
        bystander = f"sdo-assure-bystander-{tag}"
        self.kubectl.apply(pod(bystander, self.namespace, {"sdo.dev/assurance": tag}))
        return created, bystander

    def remaining_helpers(self, tag: str) -> list[str]:
        remaining = []
        for namespace in (self.namespace, self.control_namespace):
            listed = self.kubectl.json(
                "get", "pods,jobs", "--selector", f"{HELPER_LABEL}=true,sdo.dev/assurance={tag}", namespace=namespace
            )
            remaining.extend(
                f"{item['kind']}/{namespace}/{item['metadata']['name']}"
                for item in listed.get("items", [])
                if not item.get("metadata", {}).get("deletionTimestamp")
            )
        return sorted(remaining)

    # --- one run ----------------------------------------------------------------------------------

    def run_single(self, case: FaultCase, *, iteration: int, hold: bool = False) -> FaultRun:
        return self._run(
            name=case.name,
            kind="single",
            faults=(case,),
            wrong_fixes=case.wrong_fixes,
            iteration=iteration,
            hold=hold,
        )

    def run_composite(self, case: CompositeCase, *, iteration: int) -> FaultRun:
        return self._run(
            name=case.name,
            kind="composite",
            faults=case.faults,
            wrong_fixes=case.wrong_fixes,
            iteration=iteration,
            hold=False,
            registry_id=case.registry_id,
        )

    def inject_case(self, case: CompositeCase) -> tuple[datetime, datetime]:
        """Make a composite case's faults live: one registry problem, or a worker composition."""

        return self._inject([fault.problem_id for fault in case.faults], registry_id=case.registry_id)

    def _inject(self, problems: list[str], *, registry_id: str = "") -> tuple[datetime, datetime]:
        # A registry composite is one SREGym problem; the worker addresses its fault components
        # as faults 0..n-1, exactly as it does a composition, so partial fixes work unchanged.
        if registry_id:
            window = self.driver.inject(registry_id)
        elif len(problems) == 1:
            window = self.driver.inject(problems[0])
        else:
            window = self.driver.inject_composite(problems).window
        return window.started_at, window.finished_at

    def _run(
        self,
        *,
        name: str,
        kind: str,
        faults: tuple[FaultCase, ...],
        wrong_fixes: tuple[WrongFix, ...],
        iteration: int,
        hold: bool,
        registry_id: str = "",
    ) -> FaultRun:
        problems = [fault.problem_id for fault in faults]
        objects = tuple(item for fault in faults for item in fault.faulted_objects)
        known_gap = "; ".join(fault.known_gap for fault in faults if fault.known_gap)
        # SREGym's recovery rolls each broken service's Deployment out again.
        recovered = {f"Deployment/{fault.service}" for fault in faults}
        self._runs += 1
        tag = f"{self._runs}-{int(time.time())}"
        bounds = self.bounds
        self.wait_settled()
        started = utcnow()
        run = FaultRun(case=name, kind=kind, iteration=iteration, problems=problems, started_at=started)  # type: ignore[arg-type]
        checks = run.checks
        injected = False
        try:
            before = self.probe("healthy before the fault", None, gate=False)
            checks.append(
                Check(
                    name="healthy before the fault",
                    passed=before.status_exit == EXIT_HEALTHY,
                    detail=f"status exit {before.status_exit}",
                )
            )
            known = self.known_requests()
            run.injection_started_at, run.injection_finished_at = self._inject(problems, registry_id=registry_id)
            injected = True
            request_file, request = self.wait_request(known=known, timeout=bounds.detect_seconds + 120)
            received = utcnow()
            run.incident_id = str(request["incident_id"])
            run.request_received_seconds = round((received - run.injection_started_at).total_seconds(), 3)
            run.first_finding_seconds = first_findings(
                self.controller, since=started, reference=run.injection_started_at
            )
            run.diff = diff_reading(request, objects)
            # A fault that lands after dispatch cannot be in the request's snapshot; the responder
            # sees it in the controller's live view, which `sdo incident status` reports.
            run.live_named = (
                self.wait_live_diff(request_file, run.diff.missing, timeout=bounds.detect_seconds)
                if run.diff.missing
                else []
            )
            unnamed = sorted(set(run.diff.missing) - set(run.live_named))
            checks.append(
                Check(
                    name="diff names every faulted object",
                    passed=not unnamed,
                    detail=f"request diff named {run.diff.named}, live view added {run.live_named}, missing {unnamed}",
                )
            )
            checks.append(
                Check(
                    name="diff names no decoy",
                    passed=not run.diff.decoys_named,
                    detail=f"decoys {run.diff.decoys_named}",
                )
            )
            checks.append(
                Check(
                    name="diff names nothing else",
                    passed=not run.diff.unexpected,
                    detail=f"unexpected {run.diff.unexpected}",
                )
            )
            if known_gap and "traffic-health" in run.first_finding_seconds:
                traffic = run.first_finding_seconds["traffic-health"]
                checks.append(
                    Check(
                        name="traffic prober detects within the bound",
                        passed=traffic <= bounds.detect_seconds,
                        known_gap=True,
                        detail=f"traffic-health first finding at {traffic:.1f}s ({known_gap})",
                    )
                )
            elif known_gap:
                checks.append(
                    Check(
                        name="traffic prober detects within the bound",
                        passed=False,
                        known_gap=True,
                        detail=f"traffic-health never produced a finding ({known_gap})",
                    )
                )

            run.on_fault = self.probe("on the fault", request_file)
            checks.append(
                Check(
                    name="status unhealthy on the fault",
                    passed=run.on_fault.status_exit == EXIT_UNHEALTHY,
                    detail=f"exit {run.on_fault.status_exit}",
                )
            )
            checks.append(
                Check(
                    name="gate refuses on the fault",
                    passed=run.on_fault.gate_exit == EXIT_GATE_REFUSED,
                    detail=f"exit {run.on_fault.gate_exit}",
                )
            )
            held_from = utcnow()
            if hold:
                # Stay faulty past the settle period, so an absorbing baseline would take the fault in.
                remaining = (run.injection_finished_at + timedelta(seconds=STATE_SETTLE_SECONDS + 15)) - utcnow()
                time.sleep(max(0.0, remaining.total_seconds()))

            actions: list[dict[str, Any]] = []
            touched: set[str] = set()
            for number, fix in enumerate(wrong_fixes, start=1):
                began = utcnow()
                applied, detail = self.apply_wrong_fix(fix)
                if fix.kind == "restart":
                    touched.add(f"Deployment/{fix.target}")
                actions.append(
                    _action(
                        f"wrong-{number}",
                        target=fix.target or "mongodb",
                        summary=fix.label,
                        started=began,
                        success=False,
                    )
                )
                probe = self.probe(f"after wrong fix: {fix.label}", request_file)
                run.wrong_fixes.append(WrongFixResult(label=fix.label, applied=applied, detail=detail, probe=probe))
                checks.append(
                    Check(
                        name=f"still unhealthy after {fix.label}",
                        passed=probe.status_exit == EXIT_UNHEALTHY,
                        detail=f"exit {probe.status_exit}",
                    )
                )
                checks.append(
                    Check(
                        name=f"gate refuses after {fix.label}",
                        passed=probe.gate_exit == EXIT_GATE_REFUSED,
                        detail=f"exit {probe.gate_exit}",
                    )
                )

            if len(problems) > 1:
                # Fix every fault but the last: the application must stay unhealthy.
                for index in range(len(problems) - 1):
                    began = utcnow()
                    self.driver.recover_fault(index)
                    actions.append(
                        _action(
                            f"partial-{index}",
                            target=problems[index],
                            summary=f"recover {problems[index]}",
                            started=began,
                            success=True,
                        )
                    )
                    probe = self.probe(f"after fixing {problems[index]}", request_file)
                    remaining = problems[index + 1 :]
                    run.partial_fixes.append(
                        PartialFixResult(fixed_problem=problems[index], remaining_problems=remaining, probe=probe)
                    )
                    checks.append(
                        Check(
                            name=f"still unhealthy with {', '.join(remaining)} left",
                            passed=probe.status_exit == EXIT_UNHEALTHY,
                            detail=f"exit {probe.status_exit}",
                        )
                    )
                    checks.append(
                        Check(
                            name=f"gate refuses with {', '.join(remaining)} left",
                            passed=probe.gate_exit == EXIT_GATE_REFUSED,
                            detail=f"exit {probe.gate_exit}",
                        )
                    )

            began = utcnow()
            run.correct_fix_seconds = round(self.driver.recover(), 3)
            fixed_at = utcnow()
            injected = False
            actions.append(
                _action(
                    "correct",
                    target=",".join(objects),
                    summary=f"recover {', '.join(problems)}",
                    started=began,
                    success=True,
                )
            )
            clear_deadline = time.monotonic() + bounds.clear_seconds + 60
            while True:
                if self.status(request_file).exit_code == EXIT_HEALTHY:
                    run.clear_seconds = round((utcnow() - fixed_at).total_seconds(), 3)
                    break
                if time.monotonic() >= clear_deadline:
                    break
                time.sleep(0.5)
            checks.append(
                Check(
                    name="status healthy within the clear bound",
                    passed=run.clear_seconds is not None and run.clear_seconds <= bounds.clear_seconds,
                    detail=f"cleared after {run.clear_seconds}s (bound {bounds.clear_seconds}s)",
                )
            )
            run.after_fix = self.probe("after the correct fix", request_file)
            checks.append(
                Check(
                    name="gate accepts after the correct fix",
                    passed=run.after_fix.gate_exit == 0,
                    detail=f"exit {run.after_fix.gate_exit}",
                )
            )

            # Some SREGym recoveries delete every pod in the namespace, so the responder's helpers
            # are created after the correct fix, just before it returns.
            helpers, bystander = self.create_helpers(tag)
            result = scripted_result(
                request,
                objects=objects,
                summary=f"{', '.join(problems)} changed {', '.join(objects)}",
                actions=actions,
                started_at=received,
                verification=[
                    {
                        "name": "sdo incident status",
                        "passed": True,
                        "details": "exit 0 after the correct fix",
                        "observed_at": utcnow().isoformat(),
                    }
                ],
            )
            result_path(self.spool, run.incident_id).write_text(json.dumps(result), encoding="utf-8")
            exited = self._wait_exited(run.incident_id, timeout=30)
            cleanup_seconds = None
            deadline = time.monotonic() + bounds.cleanup_seconds + 30
            remaining_helpers = self.remaining_helpers(tag)
            while remaining_helpers and time.monotonic() < deadline:
                time.sleep(0.1)
                remaining_helpers = self.remaining_helpers(tag)
            if not remaining_helpers:
                cleanup_seconds = round((utcnow() - exited).total_seconds(), 3)

            ledger = self.wait_closure(run.incident_id, timeout=bounds.verify_seconds + 120)
            closure = ledger.get("closure") or {}
            verified_at = datetime.fromisoformat(str(closure["verified_at"]).replace("Z", "+00:00"))
            detected_at = datetime.fromisoformat(str(closure["detected_at"]).replace("Z", "+00:00"))
            run.verified_seconds = round((verified_at - fixed_at).total_seconds(), 3)
            run.incident_opened_seconds = round((detected_at - run.injection_started_at).total_seconds(), 3)
            run.closure_acknowledged = True
            run.final_detector_states = {
                str(state["detector_id"]): str(state["status"]) for state in closure.get("final_detector_states") or []
            }
            cleaned = sorted(closure.get("cleaned_helpers") or [])
            bystander_kept = (
                self.kubectl.run("get", "pod", bystander, namespace=self.namespace, check=False).returncode == 0
            )
            self.kubectl.run("delete", "pod", bystander, "--wait=false", namespace=self.namespace, check=False)
            run.helpers = HelperResult(
                created=helpers,
                cleaned_by_controller=cleaned,
                remaining=remaining_helpers,
                bystander_kept=bystander_kept,
                cleanup_seconds=cleanup_seconds,
            )
            run.diagnosis_verdicts = self._verdicts(run.incident_id)

            checks.append(
                Check(
                    name="incident opened within the detect bound",
                    passed=run.incident_opened_seconds <= bounds.detect_seconds,
                    detail=f"opened {run.incident_opened_seconds}s after injection started",
                )
            )
            checks.append(
                Check(
                    name="controller verified recovery within the bound",
                    passed=run.verified_seconds <= bounds.verify_seconds,
                    detail=f"verified {run.verified_seconds}s after the fix",
                )
            )
            checks.append(
                Check(
                    name="closure never preceded the correct fix",
                    passed=verified_at >= fixed_at,
                    detail=f"verified_at {verified_at.isoformat()}",
                )
            )
            checks.append(
                Check(
                    name="every health detector clear at closure",
                    passed=bool(run.final_detector_states) and set(run.final_detector_states.values()) == {"clear"},
                    detail=str(run.final_detector_states),
                )
            )
            checks.append(
                Check(
                    name="labelled helpers cleaned",
                    passed=not remaining_helpers and set(helpers) <= set(cleaned),
                    detail=f"cleaned {cleaned}, remaining {remaining_helpers}",
                )
            )
            checks.append(
                Check(
                    name="helper cleanup within the bound",
                    passed=cleanup_seconds is not None and cleanup_seconds <= bounds.cleanup_seconds,
                    detail=f"{cleanup_seconds}s after the responder exited",
                )
            )
            checks.append(Check(name="unlabelled bystander kept", passed=bystander_kept))
            checks.append(
                Check(
                    name="diagnosis verified as confirmed",
                    passed=bool(run.diagnosis_verdicts) and set(run.diagnosis_verdicts) == {"confirmed"},
                    detail=str(run.diagnosis_verdicts),
                )
            )
            if hold:
                run.absorption = self._absorption(problems, objects, touched | recovered, held_from)
                checks.append(
                    Check(
                        name="baseline did not absorb the held fault",
                        passed=not run.absorption.reinjected_diff.missing
                        and not run.absorption.reinjected_diff.decoys_named,
                        detail=f"re-injected diff named {run.absorption.reinjected_diff.named}",
                    )
                )
        except Exception as exc:
            logger.exception("assurance run %s failed", name)
            run.error = f"{type(exc).__name__}: {exc}"
            if injected:
                try:
                    self.driver.recover()
                except Exception as recover_exc:
                    run.error += f"; recovery failed: {recover_exc}"
            self._release_open_responders()
        return run

    def _absorption(
        self, problems: list[str], objects: tuple[str, ...], touched: set[str], held_from: datetime
    ) -> AbsorptionResult:
        """Re-inject right after closure: the diff against the untouched baseline must name the fault again."""

        held = round((utcnow() - held_from).total_seconds(), 1)
        since = utcnow()
        known = self.known_requests()
        self._inject(problems)
        try:
            request_file, request = self.wait_request(known=known, timeout=self.bounds.detect_seconds + 120)
            # The baseline still predates the first fault, so what the wrong fixes and the recovery
            # rolled out also shows; those objects are allowed, nothing else is.
            diff = diff_reading(request, objects, allowed=tuple(sorted(touched)))
        finally:
            self.driver.recover()
        incident_id = str(request["incident_id"])
        result = scripted_result(
            request,
            objects=objects,
            summary="re-injected fault",
            actions=[_action("correct", target=",".join(objects), summary="recover", started=since, success=True)],
            started_at=since,
            verification=[],
        )
        result_path(self.spool, incident_id).write_text(json.dumps(result), encoding="utf-8")
        self.wait_closure(incident_id, timeout=self.bounds.verify_seconds + 120)
        del request_file
        return AbsorptionResult(held_seconds=held, reinjected_diff=diff, reinjected_incident_id=incident_id)

    def _wait_exited(self, incident_id: str, *, timeout: float) -> datetime:
        path = exited_path(self.spool, incident_id)
        deadline = time.monotonic() + timeout
        while not path.is_file():
            if time.monotonic() >= deadline:
                raise HarnessError(f"responder for {incident_id} did not exit")
            time.sleep(0.05)
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())

    def _verdicts(self, incident_id: str) -> list[str]:
        outcomes = self.app_root / ".sdo" / "outcomes.jsonl"
        for line in reversed(outcomes.read_text(encoding="utf-8").splitlines()):
            record = json.loads(line)
            if record.get("incident_id") == incident_id:
                return [str(item.get("verdict")) for item in record.get("diagnosis_verification") or []]
        return []

    def _release_open_responders(self) -> None:
        """Answer any responder still waiting, so a failed run cannot wedge the controller."""

        for path in self.spool.glob("*.request.json"):
            request = json.loads(path.read_text(encoding="utf-8"))
            incident_id = str(request["incident_id"])
            if not exited_path(self.spool, incident_id).exists() and not result_path(self.spool, incident_id).exists():
                failed = scripted_result(
                    request,
                    objects=(),
                    summary="assurance run aborted",
                    actions=[],
                    started_at=utcnow(),
                    verification=[],
                )
                failed["status"] = "failed"
                failed["confirmed_root_causes"] = []
                result_path(self.spool, incident_id).write_text(json.dumps(failed), encoding="utf-8")


def request_file_for(spool: Path, incident_id: str) -> Path:
    return request_path(spool, incident_id)
