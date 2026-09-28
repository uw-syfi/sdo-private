"""One long-running SDO controller per application across SREGym problems.

SREGym redeploys the application for every problem: the conductor deletes the
application namespace after each problem and recreates it before the next.
The paper's controller is long-running, so in persistent mode the controller,
its repository PVC, state ConfigMap, Lease, credentials, and responder and
validator Jobs live in a dedicated per-application namespace
(``<application-namespace>-sdo``) that observes the application namespace
through namespace-scoped RBAC. Between problems the adapter pauses the
controller through its maintenance ConfigMap; the next problem re-grants
access to the recreated namespace and resumes it with a new generation.

Reflection is asynchronous: a problem ends as soon as the controller has
independently verified recovery, and the next problem (or pipeline teardown)
waits for that incident's reflection and detector rollout before injecting,
then writes the earlier problem's strict receipt. The wait is reported as a
pre-injection ``reflection_drain`` cost, never as resolution time.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.sregym.adapter.fault_gate import inject_fault_after_resumed_baseline
from benchmarks.sregym.adapter.runtime import (
    RUNTIME_ARTIFACTS_DIRNAME,
    RuntimeConfig,
    collect_production_receipt,
    export_controller_logs,
    export_runtime_artifacts,
    install_persistent_controller,
    validate_production_receipt,
)
from sdo.agent_runtime.lifecycle import validation_report
from sdo.controller_install import (
    CONTROLLER_JOB_NAME,
    ControllerInstallError,
    kubectl,
    set_controller_maintenance,
    start_repository_sync,
    stop_repository_sync,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sdo.agent_runtime.lifecycle import LifecycleValidationCache

logger = logging.getLogger(__name__)

#: Path of the pipeline-scoped JSON state file; set by the runner for every stage.
PERSISTENT_STATE_ENV = "SDO_PERSISTENT_CONTROLLER_STATE"
CONTROL_NAMESPACE_SUFFIX = "-sdo"
RESOLUTION_FILENAME = "sdo_incident_resolution.json"
STRICT_RECEIPT_FILENAME = "sdo_production_receipt_strict.json"
# A drained receipt that failed production validation, kept with its error for analysis.
REJECTED_RECEIPT_FILENAME = "sdo_rejected_production_receipt.json"
CONTROLLER_LOGS_SUBDIR = Path(RUNTIME_ARTIFACTS_DIRNAME) / "controller_logs"
POLL_SECONDS = 1.0
MAINTENANCE_ACK_TIMEOUT_SECONDS = 600.0
DRAIN_TIMEOUT_SECONDS = 3600.0
STAGE_END_EVIDENCE_SCOPE = "stage-end snapshot; the drained strict receipt supersedes it"
_ZERO_TIME = "0001-01-01T00:00:00Z"


class PersistentControllerError(RuntimeError):
    """Raised when a persistent controller cannot be installed, reused, or drained."""


class ClosureFailedError(PersistentControllerError):
    """Raised when the controller gave up committing an incident closure the broker kept rejecting."""


def control_namespace_for(app_namespace: str) -> str:
    """Name the per-application SDO namespace after the application namespace."""

    name = f"{app_namespace}{CONTROL_NAMESPACE_SUFFIX}"
    if len(name) > 63:
        raise PersistentControllerError(f"application namespace {app_namespace!r} is too long for {name!r}")
    return name


class PendingIncident(BaseModel):
    """A verified incident whose reflection may still be running."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    stage_label: str
    receipt_dir: Path
    repository: Path
    verified_at: datetime | None
    stage_receipt: dict[str, Any]


class ControllerRecord(BaseModel):
    """One application's controller as installed by this pipeline."""

    model_config = ConfigDict(extra="forbid")

    application: str
    namespace: str
    control_namespace: str
    lifecycle_fingerprint: str
    controller_pod_uid: str
    controller_pod_name: str
    installed_by_stage: str
    installed_at: datetime
    kubeconfig: str | None = None
    runtime_config: dict[str, Any]
    served_stages: list[str] = Field(default_factory=list)
    pending: PendingIncident | None = None


class DeferredReceipt(BaseModel):
    """Where a stage's receipt is written after its drain, for publication into the results tree."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    stage_label: str
    staging_dir: Path


class PersistentState(BaseModel):
    """Pipeline-scoped registry of persistent controllers, keyed by application namespace."""

    model_config = ConfigDict(extra="forbid")

    controllers: dict[str, ControllerRecord] = Field(default_factory=dict)
    deferred_receipts: list[DeferredReceipt] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> PersistentState:
        if not path.is_file():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


@dataclass(frozen=True)
class ControllerPod:
    name: str
    uid: str


@dataclass(frozen=True)
class VerifiedIncident:
    incident_id: str
    closure: dict[str, Any] | None


def runtime_config_payload(config: RuntimeConfig) -> dict[str, Any]:
    payload = {field: getattr(config, field) for field in RuntimeConfig.__dataclass_fields__}
    return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}


def runtime_config_from_payload(payload: dict[str, Any]) -> RuntimeConfig:
    values = dict(payload)
    values["repository"] = Path(values["repository"])
    if values.get("artifacts_dir") is not None:
        values["artifacts_dir"] = Path(values["artifacts_dir"])
    return RuntimeConfig(**values)


class ClusterOps(Protocol):
    """Cluster side effects of persistent mode; replaced by a fake in tests."""

    def controller_pod(self, control_namespace: str) -> ControllerPod | None: ...

    def namespace_exists(self, namespace: str) -> bool: ...

    def delete_controller(self, control_namespace: str) -> None: ...

    def install(self, config: RuntimeConfig) -> bool: ...

    def set_maintenance(self, control_namespace: str, *, paused: bool, generation: str) -> None: ...

    def runtime_state(self, control_namespace: str) -> dict[str, Any]: ...

    def controller_logs(self, control_namespace: str) -> str: ...

    def inject_after_resume(
        self, control_namespace: str, generation: str, inject: Callable[[], None]
    ) -> dict[str, float]: ...

    def collect_receipt(self, config: RuntimeConfig, incident_id: str, artifacts_dir: Path) -> dict[str, Any]: ...

    def export_runtime_artifacts(self, config: RuntimeConfig, artifacts_dir: Path) -> dict[str, str | None]: ...

    def export_controller_logs(self, control_namespace: str, artifacts_dir: Path) -> None: ...


class KubectlClusterOps:
    """Production implementation over kubectl and the SDO controller installer."""

    def controller_pod(self, control_namespace: str) -> ControllerPod | None:
        completed = kubectl(
            ["get", "pods", "--selector", f"job-name={CONTROLLER_JOB_NAME}", "-o", "json"],
            namespace=control_namespace,
            check=False,
        )
        if completed.returncode != 0:
            return None
        running = [
            item
            for item in json.loads(completed.stdout).get("items", [])
            if isinstance(item, dict) and item.get("status", {}).get("phase") == "Running"
        ]
        if len(running) != 1:
            return None
        metadata = running[0].get("metadata", {})
        return ControllerPod(name=str(metadata.get("name", "")), uid=str(metadata.get("uid", "")))

    def namespace_exists(self, namespace: str) -> bool:
        return kubectl(["get", f"namespace/{namespace}"], namespace=None, check=False).returncode == 0

    def delete_controller(self, control_namespace: str) -> None:
        kubectl(
            ["delete", f"namespace/{control_namespace}", "--ignore-not-found=true", "--wait=true", "--timeout=300s"],
            namespace=None,
        )

    def install(self, config: RuntimeConfig) -> bool:
        return install_persistent_controller(config)

    def set_maintenance(self, control_namespace: str, *, paused: bool, generation: str) -> None:
        set_controller_maintenance(control_namespace, paused=paused, generation=generation)

    def runtime_state(self, control_namespace: str) -> dict[str, Any]:
        completed = kubectl(
            ["get", "configmap/sdo-controller-state", "-o", "json"], namespace=control_namespace, check=False
        )
        if completed.returncode != 0:
            return {}
        document = json.loads(completed.stdout)
        state = json.loads(document.get("data", {}).get("runtime-state.json", "{}"))
        return state if isinstance(state, dict) else {}

    def controller_logs(self, control_namespace: str) -> str:
        pod = self.controller_pod(control_namespace)
        if pod is None:
            return ""
        return kubectl(
            ["logs", f"pod/{pod.name}", "--container=controller"], namespace=control_namespace, check=False
        ).stdout

    def inject_after_resume(
        self, control_namespace: str, generation: str, inject: Callable[[], None]
    ) -> dict[str, float]:
        return inject_fault_after_resumed_baseline(control_namespace, generation, inject=inject, kubectl_runner=kubectl)

    def collect_receipt(self, config: RuntimeConfig, incident_id: str, artifacts_dir: Path) -> dict[str, Any]:
        start_repository_sync(config)
        try:
            return collect_production_receipt(config, incident_id, artifacts_dir)
        finally:
            stop_repository_sync(config)

    def export_runtime_artifacts(self, config: RuntimeConfig, artifacts_dir: Path) -> dict[str, str | None]:
        """Snapshot usage logs and transcripts at stage end; diagnostic, so errors are returned.

        The repository sync pod is left running: the drain that follows applies
        the same pod and removes it, so no stage waits on a terminating pod.
        """

        try:
            start_repository_sync(config)
            return export_runtime_artifacts(config.control_namespace, artifacts_dir)
        except (ControllerInstallError, OSError, subprocess.SubprocessError) as exc:
            return {"directory": None, "error": f"{type(exc).__name__}: {exc}"}

    def export_controller_logs(self, control_namespace: str, artifacts_dir: Path) -> None:
        export_controller_logs(control_namespace, artifacts_dir)


@dataclass(frozen=True)
class StageInputs:
    stage_label: str
    application: str
    namespace: str
    lifecycle_fingerprint: str
    runtime_config: RuntimeConfig
    receipt_dir: Path
    state_path: Path
    kubeconfig: str | None = None
    verification_timeout_seconds: float = 3900.0
    # Opt-in shared lifecycle validation verdicts; run_lifecycle consults it.
    validation_cache: LifecycleValidationCache | None = None


@dataclass(frozen=True)
class Clock:
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def run_persistent_stage(
    inputs: StageInputs,
    *,
    ops: ClusterOps,
    run_lifecycle: Callable[[], bool],
    inject: Callable[[], None],
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Run one benchmark problem against the application's persistent controller.

    Returns the stage's resolution record. It ends at controller-verified
    recovery; the strict receipt, which needs the finished reflection, is
    written later by :func:`drain_pending_incident`.
    """

    clock = clock or Clock()
    started = clock.monotonic()
    config = replace(inputs.runtime_config, persistent=True, wait_for_completion=False)
    control = config.control_namespace
    if control != control_namespace_for(inputs.namespace):
        raise PersistentControllerError(f"controller namespace {control!r} is not owned by {inputs.namespace!r}")
    state = PersistentState.load(inputs.state_path)
    record = state.controllers.get(inputs.namespace)
    if record is not None and record.application != inputs.application:
        raise PersistentControllerError(
            f"namespace {inputs.namespace!r} is operated by the persistent controller of "
            f"{record.application!r}; refusing to reuse it for {inputs.application!r}"
        )
    pod = ops.controller_pod(control) if record is not None else None
    drain_seconds = 0.0
    if record is not None and pod is not None and pod.uid == record.controller_pod_uid and record.pending is not None:
        # The previous problem's learning must reach memory (and any learned
        # detector the controller) before this problem's fault is injected.
        drain_seconds = drain_pending_incident(
            record, ops=ops, repository=config.repository, drained_by=inputs.stage_label, clock=clock
        )
        record.pending = None
        state.save(inputs.state_path)
        # Publish the drained receipt now, so a pipeline stopped during this
        # stage keeps the previous stage's strict receipt and token evidence.
        _publish_drained_receipts(inputs.state_path)
    reusable = (
        record is not None
        and pod is not None
        and pod.uid == record.controller_pod_uid
        and record.lifecycle_fingerprint == inputs.lifecycle_fingerprint
    )
    if reusable:
        lifecycle_reused, lifecycle_revalidation_skipped = True, True
    else:
        if record is not None and record.pending is not None:
            raise PersistentControllerError(
                f"cannot replace the controller of {inputs.namespace!r}: incident {record.pending.incident_id!r} "
                "has not been drained into operational memory"
            )
        if ops.namespace_exists(control):
            # Never adopt a controller this pipeline did not install and validate.
            ops.delete_controller(control)
        state.controllers.pop(inputs.namespace, None)
        state.save(inputs.state_path)
        lifecycle_reused, lifecycle_revalidation_skipped = run_lifecycle(), False
    lifecycle_ready = clock.monotonic()
    reused = ops.install(replace(config, reuse_existing=reusable))
    if reusable and not reused:
        raise PersistentControllerError("the running controller's install fingerprint changed mid-pipeline")
    pod = _wait_for_controller_pod(ops, control, clock)
    if record is None or not reusable:
        record = ControllerRecord(
            application=inputs.application,
            namespace=inputs.namespace,
            control_namespace=control,
            lifecycle_fingerprint=inputs.lifecycle_fingerprint,
            controller_pod_uid=pod.uid,
            controller_pod_name=pod.name,
            installed_by_stage=inputs.stage_label,
            installed_at=clock.now(),
            kubeconfig=inputs.kubeconfig,
            runtime_config=runtime_config_payload(config),
        )
    elif pod.uid != record.controller_pod_uid:
        raise PersistentControllerError("the persistent controller pod was replaced mid-pipeline")
    state.controllers[inputs.namespace] = record
    state.save(inputs.state_path)
    known = _known_incident_ids(ops.runtime_state(control))
    install_ready = clock.monotonic()
    generation = f"{inputs.stage_label}-{uuid.uuid4().hex[:8]}"
    ops.set_maintenance(control, paused=False, generation=generation)
    gate_timings = ops.inject_after_resume(control, generation, inject)
    injected = clock.monotonic()
    verified = _wait_for_verified_incident(ops, control, known, inputs.verification_timeout_seconds, clock)
    verified_ready = clock.monotonic()
    paused_generation = f"{generation}-paused"
    ops.set_maintenance(control, paused=True, generation=paused_generation)
    _wait_for_maintenance_ack(ops, control, paused_generation, clock)
    paused_ready = clock.monotonic()
    ops.export_controller_logs(control, inputs.receipt_dir)
    stage_evidence: dict[str, Any] = dict(ops.export_runtime_artifacts(config, inputs.receipt_dir))
    stage_evidence["scope"] = STAGE_END_EVIDENCE_SCOPE
    closure = verified.closure or {}
    raw_result = closure.get("result")
    result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
    resolution: dict[str, Any] = {
        "schema_version": "sdo.sregym-incident-resolution/v1",
        "incident_id": verified.incident_id,
        "namespace": inputs.namespace,
        "confirmed_root_causes": result.get("confirmed_root_causes", []),
        "repair_actions": result.get("repair_actions", []),
        # Names the responder's rollout in the stage-end evidence.
        "responder_session_id": result.get("responder_session_id"),
        "lifecycle_reused": lifecycle_reused,
        "lifecycle_validation": validation_report(inputs.validation_cache),
        "fault_injection_deferred": True,
        "fault_gate_timings_seconds": gate_timings,
        "persistent_controller": {
            "control_namespace": control,
            "controller_pod_uid": pod.uid,
            "controller_pod_name": pod.name,
            "installed_this_stage": not reusable,
            "installed_by_stage": record.installed_by_stage,
            "lifecycle_revalidation_skipped": lifecycle_revalidation_skipped,
            "maintenance_generation": generation,
            "stage_label": inputs.stage_label,
        },
        "pre_injection_costs_seconds": {
            "previous_incident_reflection_drain": drain_seconds,
            "inventory_and_lifecycle": lifecycle_ready - started - drain_seconds,
            "controller_install_or_reuse": install_ready - lifecycle_ready,
            "controller_baseline_wait": gate_timings.get("controller_baseline_wait", 0.0),
        },
        "reflection_drain_seconds": drain_seconds,
        "driver_phase_timings_seconds": {
            "reflection_drain": drain_seconds,
            "inventory_and_lifecycle": lifecycle_ready - started - drain_seconds,
            "controller_install_or_reuse": install_ready - lifecycle_ready,
            "fault_gate": injected - install_ready,
            "injection_to_verified_recovery": verified_ready - injected,
            "pause_for_redeploy": paused_ready - verified_ready,
            "driver_total_before_submission": paused_ready - started,
        },
    }
    resolution["stage_end_runtime_artifacts"] = stage_evidence
    resolution.update(_resolution_timings(closure))
    record.pending = PendingIncident(
        incident_id=verified.incident_id,
        stage_label=inputs.stage_label,
        receipt_dir=inputs.receipt_dir,
        repository=config.repository,
        verified_at=_timestamp(closure.get("verified_at")),
        stage_receipt={
            key: value
            for key, value in resolution.items()
            if key
            not in {
                "schema_version",
                "confirmed_root_causes",
                "repair_actions",
                "namespace",
                "incident_id",
                "responder_session_id",
                "stage_end_runtime_artifacts",
            }
        },
    )
    record.served_stages.append(inputs.stage_label)
    state.deferred_receipts.append(
        DeferredReceipt(
            incident_id=verified.incident_id, stage_label=inputs.stage_label, staging_dir=inputs.receipt_dir
        )
    )
    state.save(inputs.state_path)
    persist_resolution(resolution, inputs.receipt_dir)
    return resolution


def drain_pending_incident(
    record: ControllerRecord,
    *,
    ops: ClusterOps,
    repository: Path,
    drained_by: str,
    clock: Clock | None = None,
    timeout_seconds: float = DRAIN_TIMEOUT_SECONDS,
) -> float:
    """Wait for a verified incident's reflection and rollout, then write its strict receipt.

    ``repository`` receives the controller's operational repository, which is
    the source of truth across problems. Returns the seconds spent waiting.
    """

    clock = clock or Clock()
    pending = record.pending
    if pending is None:
        return 0.0
    started = clock.monotonic()
    try:
        _wait_for_reflection_drain(ops, record.control_namespace, pending.incident_id, timeout_seconds, clock)
    except ClosureFailedError:
        # The controller log holds every broker rejection of the closure.
        ops.export_controller_logs(record.control_namespace, pending.receipt_dir)
        raise
    waited = clock.monotonic() - started
    config = replace(
        runtime_config_from_payload(record.runtime_config),
        repository=repository,
        artifacts_dir=pending.receipt_dir,
        persistent=True,
    )
    receipt = ops.collect_receipt(config, pending.incident_id, pending.receipt_dir)
    receipt.update(pending.stage_receipt)
    recovery = receipt.get("phase_timings_seconds", {}).get("operational_recovery")
    if "incident_resolution_seconds" not in receipt and isinstance(recovery, (int, float)):
        receipt["incident_resolution_seconds"] = float(recovery)
        receipt["incident_resolution_scope"] = "detected_to_independently_verified_health"
    # Resolution ends at verified health. Setup before injection and learning
    # after verification (including this drain) are reported, never included.
    receipt["excluded_from_incident_resolution_seconds"] = {
        **pending.stage_receipt.get("pre_injection_costs_seconds", {}),
        "post_recovery_learning_and_receipt": receipt.get("phase_timings_seconds", {}).get(
            "post_recovery_learning_and_receipt"
        ),
    }
    receipt["reflection_drain"] = {"drained_by": drained_by, "waited_seconds": waited}
    # The controller log covering reflection is evidence whether or not the receipt validates.
    ops.export_controller_logs(record.control_namespace, pending.receipt_dir)
    try:
        validate_production_receipt(receipt, allow_test_lifecycle=config.allow_test_lifecycle)
    except ControllerInstallError as exc:
        _write_json({"validation_error": str(exc), "receipt": receipt}, pending.receipt_dir / REJECTED_RECEIPT_FILENAME)
        raise
    persist_strict_receipt(receipt, pending.receipt_dir)
    return waited


def teardown(state_path: Path, *, ops: ClusterOps, clock: Clock | None = None) -> list[str]:
    """Drain every pending incident, stop every persistent controller, and return errors."""

    state = PersistentState.load(state_path)
    errors: list[str] = []
    for namespace, record in list(state.controllers.items()):
        try:
            pod = ops.controller_pod(record.control_namespace)
            if record.pending is not None:
                if pod is None or pod.uid != record.controller_pod_uid:
                    raise PersistentControllerError("controller pod is gone before its last incident was drained")
                drain_pending_incident(
                    record, ops=ops, repository=record.pending.repository, drained_by="pipeline-teardown", clock=clock
                )
                record.pending = None
                state.save(state_path)
        except (PersistentControllerError, ControllerInstallError, OSError, ValueError) as exc:
            errors.append(f"{namespace}: {exc}")
        finally:
            ops.delete_controller(record.control_namespace)
            state.controllers.pop(namespace, None)
            state.save(state_path)
    return errors


def _publish_drained_receipts(state_path: Path) -> None:
    """Publish every drained receipt into the pipeline's results; teardown retries what fails."""

    try:
        for path in publish_deferred_receipts(state_path, state_path.parent):
            logger.info("published drained artifact %s", path)
    except OSError as exc:
        logger.warning("could not publish drained receipts yet: %s", exc)


def publish_deferred_receipts(state_path: Path, results_root: Path) -> list[Path]:
    """Copy drained receipts and controller logs into the harness's published run directories.

    SREGym publishes a stage's staging tree (``.runtime/<agent>/<opaque id>``)
    to ``results/<agent>/<problem>/run_N`` when the stage ends, before the
    drain can write the strict receipt. Each published run is matched to its
    incident by the resolution record it already contains, and the opaque id is
    replaced by the problem id, as the harness does at publication.
    """

    state = PersistentState.load(state_path)
    deferred = {receipt.incident_id: receipt for receipt in state.deferred_receipts}
    published: list[Path] = []
    for resolution_path in sorted(results_root.rglob(RESOLUTION_FILENAME)):
        run_dir = resolution_path.parent
        try:
            incident_id = json.loads(resolution_path.read_text(encoding="utf-8")).get("incident_id")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        entry = deferred.get(incident_id) if isinstance(incident_id, str) else None
        if entry is None or entry.staging_dir.resolve() == run_dir.resolve() or not entry.staging_dir.is_dir():
            continue
        sources = [entry.staging_dir / STRICT_RECEIPT_FILENAME, entry.staging_dir / REJECTED_RECEIPT_FILENAME]
        logs_dir = entry.staging_dir / CONTROLLER_LOGS_SUBDIR
        if logs_dir.is_dir():
            sources.extend(sorted(path for path in logs_dir.iterdir() if path.is_file()))
        # Drain-time runtime evidence (usage logs, agent transcripts) is published
        # byte for byte; per-incident analysis scopes the cumulative logs itself.
        runtime_dir = entry.staging_dir / RUNTIME_ARTIFACTS_DIRNAME
        evidence = (
            sorted(path for path in runtime_dir.rglob("*") if path.is_file() and logs_dir not in path.parents)
            if runtime_dir.is_dir()
            else []
        )
        for source in [*sources, *evidence]:
            if not source.is_file():
                continue
            target = run_dir / source.relative_to(entry.staging_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            if source in sources:
                text = source.read_text(encoding="utf-8").replace(entry.staging_dir.name, run_dir.parent.name)
                temporary.write_text(text, encoding="utf-8")
            else:
                shutil.copyfile(source, temporary)
            temporary.replace(target)
            source.unlink()
            published.append(target)
        _remove_empty_directories(entry.staging_dir)
    return published


def _remove_empty_directories(root: Path) -> None:
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def persist_resolution(resolution: dict[str, Any], receipt_dir: Path) -> Path:
    return _write_json(resolution, receipt_dir / RESOLUTION_FILENAME)


def persist_strict_receipt(receipt: dict[str, Any], receipt_dir: Path) -> Path:
    return _write_json(receipt, receipt_dir / STRICT_RECEIPT_FILENAME)


def _write_json(document: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or value == _ZERO_TIME:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolution_timings(closure: dict[str, Any]) -> dict[str, Any]:
    """Resolution ends at controller-verified health; reflection is never included."""

    detected = _timestamp(closure.get("detected_at"))
    dispatched = _timestamp(closure.get("dispatched_at"))
    responder_completed = _timestamp(closure.get("responder_completed_at"))
    verified = _timestamp(closure.get("verified_at"))
    if detected is None or verified is None:
        return {}
    timings: dict[str, float] = {"operational_recovery": (verified - detected).total_seconds()}
    if dispatched is not None:
        timings["detection_to_dispatch"] = (dispatched - detected).total_seconds()
    if dispatched is not None and responder_completed is not None:
        timings["responder"] = (responder_completed - dispatched).total_seconds()
    if responder_completed is not None:
        timings["verification"] = (verified - responder_completed).total_seconds()
    return {
        "incident_resolution_seconds": timings["operational_recovery"],
        "incident_resolution_scope": "detected_to_independently_verified_health",
        "resolution_phase_timings_seconds": timings,
        "verified_at": verified.isoformat(),
    }


def _known_incident_ids(state: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    acknowledged = state.get("last_acknowledged_incident_id")
    if isinstance(acknowledged, str) and acknowledged:
        known.add(acknowledged)
    for key in ("pending_closure", "incident_request"):
        document = state.get(key)
        if key == "pending_closure" and isinstance(document, dict):
            document = document.get("request")
        if isinstance(document, dict) and isinstance(document.get("incident_id"), str):
            known.add(document["incident_id"])
    return known


def _wait_for_controller_pod(ops: ClusterOps, control: str, clock: Clock, timeout: float = 300.0) -> ControllerPod:
    deadline = clock.monotonic() + timeout
    while clock.monotonic() < deadline:
        pod = ops.controller_pod(control)
        if pod is not None:
            return pod
        clock.sleep(POLL_SECONDS)
    raise PersistentControllerError(f"no running controller pod in {control!r}")


def _wait_for_verified_incident(
    ops: ClusterOps, control: str, known: set[str], timeout: float, clock: Clock
) -> VerifiedIncident:
    """Return the first new incident the controller verified healthy, before reflection finishes."""

    deadline = clock.monotonic() + timeout
    state: dict[str, Any] = {}
    while clock.monotonic() < deadline:
        state = ops.runtime_state(control)
        closure = state.get("pending_closure")
        if isinstance(closure, dict):
            raw_request = closure.get("request")
            request: dict[str, Any] = raw_request if isinstance(raw_request, dict) else {}
            incident_id = request.get("incident_id")
            if isinstance(incident_id, str) and incident_id not in known and _timestamp(closure.get("verified_at")):
                return VerifiedIncident(incident_id=incident_id, closure=closure)
        acknowledged = state.get("last_acknowledged_incident_id")
        if isinstance(acknowledged, str) and acknowledged and acknowledged not in known:
            # Reflection finished between polls; the ledger holds the closure.
            return VerifiedIncident(incident_id=acknowledged, closure=None)
        clock.sleep(POLL_SECONDS)
    raise PersistentControllerError(
        f"controller in {control!r} verified no new incident within {timeout:.0f}s{_open_incident_summary(state)}"
    )


def _open_incident_summary(state: dict[str, Any]) -> str:
    """Why the controller's open incident has not closed, from its last runtime state."""

    if not state.get("incident_open"):
        summary = f"; no incident open (dispatch state {state.get('dispatch_state') or 'unknown'!r})"
        failure = state.get("closure_failure")
        if isinstance(failure, dict) and failure.get("permanent"):
            # A permanently failed closure blocks every later incident.
            summary += (
                f"; closure of {failure.get('incident_id')!r} failed permanently: "
                f"{str(failure.get('last_error') or '').strip()[:500]}"
            )
        return summary
    request = state.get("incident_request") if isinstance(state.get("incident_request"), dict) else {}
    parts = [f"; incident {request.get('incident_id') or 'unknown'!r} is open"]
    result = state.get("incident_result")
    if isinstance(result, dict):
        parts.append(f"responder status {result.get('status')!r}")
        notes = [str(note) for note in result.get("repair_changes") or []]
        if notes:
            parts.append(f"responder notes: {'; '.join(notes)[:500]}")
    elif state.get("dispatch_error"):
        parts.append(f"dispatch error: {state['dispatch_error']}")
    else:
        parts.append(f"responder not done (dispatch state {state.get('dispatch_state')!r})")
    if state.get("detector_review_required"):
        parts.append(f"detector review required: {state.get('detector_review_reason') or 'no reason recorded'}")
    return ", ".join(parts)


def _log_records(logs: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in logs.splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _wait_for_maintenance_ack(
    ops: ClusterOps, control: str, generation: str, clock: Clock, timeout: float = MAINTENANCE_ACK_TIMEOUT_SECONDS
) -> None:
    deadline = clock.monotonic() + timeout
    while clock.monotonic() < deadline:
        records = _log_records(ops.controller_logs(control))
        if any(record.get("maintenance_generation") == generation for record in records):
            return
        clock.sleep(POLL_SECONDS)
    raise PersistentControllerError(f"controller in {control!r} did not acknowledge maintenance {generation!r}")


def _wait_for_reflection_drain(ops: ClusterOps, control: str, incident_id: str, timeout: float, clock: Clock) -> None:
    """Wait until the incident is acknowledged and the supervisor relaunched the controller.

    The supervisor relaunches only after compiling and rolling out any
    detector the reflection accepted, so the relaunch marks learned memory
    as live.
    """

    deadline = clock.monotonic() + timeout
    while clock.monotonic() < deadline:
        state = ops.runtime_state(control)
        _raise_if_closure_failed(state, incident_id)
        acknowledged = state.get("last_acknowledged_incident_id") == incident_id
        if acknowledged and _relaunched_after(_log_records(ops.controller_logs(control)), incident_id):
            return
        clock.sleep(POLL_SECONDS)
    raise PersistentControllerError(f"incident {incident_id!r} was not reflected and rolled out within {timeout:.0f}s")


def _raise_if_closure_failed(state: dict[str, Any], incident_id: str) -> None:
    """Stop waiting once the controller has given up the incident's closure; it never retries it again."""

    failure = state.get("closure_failure")
    if state.get("closure_state") != "failed" or not isinstance(failure, dict):
        return
    if failure.get("incident_id") != incident_id:
        return
    raise ClosureFailedError(
        f"incident {incident_id!r} closure permanently failed after {failure.get('attempts')} broker attempts: "
        f"{failure.get('last_error')}; {failure.get('action')}"
    )


def _relaunched_after(records: list[dict[str, Any]], incident_id: str) -> bool:
    restarted = False
    relaunched = False
    for record in records:
        if record.get("controller_closure_restart") == incident_id:
            restarted = True
        elif restarted and record.get("controller_supervisor") == "relaunch":
            relaunched = True
        elif relaunched and "controller_maintenance" in record:
            # The relaunched controller is up and has read its maintenance mode.
            return True
    return False


def _main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Persistent SDO controller lifecycle for SREGym pipelines")
    subparsers = parser.add_subparsers(dest="command", required=True)
    teardown_parser = subparsers.add_parser("teardown", help="drain pending incidents and stop every controller")
    teardown_parser.add_argument("--state", type=Path, required=True)
    teardown_parser.add_argument(
        "--publish-root",
        type=Path,
        help="pipeline directory whose published runs receive the drained receipts and controller logs",
    )
    args = parser.parse_args(argv)
    state = PersistentState.load(args.state)
    kubeconfigs = {record.kubeconfig for record in state.controllers.values() if record.kubeconfig}
    if kubeconfigs:
        os.environ["KUBECONFIG"] = sorted(kubeconfigs)[0]
    errors = teardown(args.state, ops=KubectlClusterOps())
    if args.publish_root is not None:
        for path in publish_deferred_receipts(args.state, args.publish_root):
            logger.info("published deferred artifact %s", path)
    for error in errors:
        logger.error("persistent controller teardown: %s", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(_main())
