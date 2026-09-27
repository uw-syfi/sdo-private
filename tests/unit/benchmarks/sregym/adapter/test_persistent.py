from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

from benchmarks.sregym.adapter.persistent import (
    REJECTED_RECEIPT_FILENAME,
    RESOLUTION_FILENAME,
    STRICT_RECEIPT_FILENAME,
    Clock,
    ControllerPod,
    PersistentControllerError,
    PersistentState,
    StageInputs,
    control_namespace_for,
    publish_deferred_receipts,
    run_persistent_stage,
    teardown,
)
from benchmarks.sregym.adapter.runtime import RuntimeConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DETECTED = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def _receipt(incident_id: str) -> dict[str, Any]:
    return {
        "schema_version": "sdo.production-receipt/v1",
        "pre_cutover": False,
        "validator_mode": "kubernetes-job",
        "lifecycle_provenance": True,
        "production_job_dispatch": True,
        "completed": True,
        "repair_policy": "recorded-actions",
        "repair_actions": [
            {
                "action_id": "a1",
                "kind": "kubectl",
                "target": "configmap/x",
                "summary": "restored",
                "details": "restored",
                "started_at": "2026-09-27T12:00:10+00:00",
                "completed_at": "2026-09-27T12:00:11+00:00",
                "success": True,
                "reversible": True,
            }
        ],
        "outcome_commit": "outcome",
        "reflection_commit": "reflection",
        "validator_evidence_commit": "reflection",
        "validator_execution_required": False,
        "validator_skipped_reason": "unchanged-diagnostics",
        "validator_network_policy_canaries": [],
        "same_session_reflection": True,
        "detector_clear": [{"status": "clear", "fingerprints": []}],
        "independent_verification": [{"passed": True}],
        "acknowledged": True,
        "cleaned": True,
        "remaining_worktrees": [],
        "responder_jobs": [f"sdo-{incident_id}"],
        "incident_id": incident_id,
        # Reflection took four minutes after verification; it must never
        # become resolution time.
        "phase_timings_seconds": {"operational_recovery": 40.0, "post_recovery_learning_and_receipt": 240.0},
    }


@dataclass
class FakeOps:
    """In-memory cluster: one controller pod per control namespace, driven by the test."""

    events: list[tuple[str, ...]] = field(default_factory=list)
    pods: dict[str, ControllerPod] = field(default_factory=dict)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    logs: dict[str, list[str]] = field(default_factory=dict)
    existing_namespaces: set[str] = field(default_factory=set)
    installs: int = 0
    incidents: int = 0
    # Reflection completes only for incidents listed here, which lets a test
    # prove that a stage returns before its reflection finishes.
    reflectable: set[str] = field(default_factory=set)
    # Incidents whose responder reported a non-completed status.
    incomplete: set[str] = field(default_factory=set)
    polls_until_reflected: dict[str, int] = field(default_factory=dict)

    def controller_pod(self, control_namespace: str) -> ControllerPod | None:
        return self.pods.get(control_namespace)

    def namespace_exists(self, namespace: str) -> bool:
        return namespace in self.existing_namespaces or namespace in self.pods

    def delete_controller(self, control_namespace: str) -> None:
        self.events.append(("delete", control_namespace))
        self.pods.pop(control_namespace, None)
        self.states.pop(control_namespace, None)
        self.existing_namespaces.discard(control_namespace)

    def install(self, config: RuntimeConfig) -> bool:
        control = config.control_namespace
        assert config.persistent is True
        assert config.wait_for_completion is False
        if config.reuse_existing and control in self.pods:
            self.events.append(("reuse", control))
            return True
        self.installs += 1
        self.pods[control] = ControllerPod(name=f"sdo-controller-run-{self.installs}", uid=f"uid-{self.installs}")
        self.states[control] = {}
        self.logs[control] = []
        self.events.append(("install", control))
        return False

    def set_maintenance(self, control_namespace: str, *, paused: bool, generation: str) -> None:
        mode = "paused" if paused else "active"
        self.events.append((mode, control_namespace))
        self.logs[control_namespace].append(
            json.dumps({"controller_maintenance": mode, "maintenance_generation": generation})
        )

    def runtime_state(self, control_namespace: str) -> dict[str, Any]:
        state = self.states.get(control_namespace, {})
        closure = state.get("pending_closure")
        if isinstance(closure, dict):
            incident_id = closure["request"]["incident_id"]
            remaining = self.polls_until_reflected.get(incident_id, 0)
            if remaining > 0:
                self.polls_until_reflected[incident_id] = remaining - 1
            elif incident_id in self.reflectable:
                self._finish_reflection(control_namespace, incident_id)
        return self.states.get(control_namespace, {})

    def _finish_reflection(self, control_namespace: str, incident_id: str) -> None:
        state = self.states[control_namespace]
        state.pop("pending_closure")
        state["last_acknowledged_incident_id"] = incident_id
        self.events.append(("reflected", incident_id))
        self.logs[control_namespace].extend(
            [
                json.dumps({"controller_closure_restart": incident_id}),
                json.dumps({"controller_supervisor": "relaunch", "launches": 1}),
                json.dumps({"controller_maintenance": "paused", "maintenance_generation": "relaunched"}),
            ]
        )

    def controller_logs(self, control_namespace: str) -> str:
        return "\n".join(self.logs.get(control_namespace, []))

    def inject_after_resume(
        self, control_namespace: str, generation: str, inject: Callable[[], None]
    ) -> dict[str, float]:
        assert self.logs[control_namespace][-1] == json.dumps(
            {"controller_maintenance": "active", "maintenance_generation": generation}
        )
        inject()
        self.incidents += 1
        incident_id = f"incident-{self.incidents}"
        self.events.append(("inject", control_namespace, incident_id))
        verified = DETECTED + timedelta(seconds=40)
        self.states[control_namespace]["pending_closure"] = {
            "request": {"incident_id": incident_id},
            "result": {
                "confirmed_root_causes": [{"summary": "missing ConfigMap"}],
                "repair_actions": [{"summary": "restored ConfigMap"}],
            },
            "detected_at": DETECTED.isoformat(),
            "dispatched_at": (DETECTED + timedelta(seconds=1)).isoformat(),
            "responder_completed_at": (DETECTED + timedelta(seconds=35)).isoformat(),
            "verified_at": verified.isoformat(),
        }
        return {"controller_baseline_wait": 3.0, "fault_injection_request": 0.5}

    def collect_receipt(self, config: RuntimeConfig, incident_id: str, artifacts_dir: Path) -> dict[str, Any]:
        self.events.append(("receipt", incident_id, str(config.repository)))
        # The drain exports the controller PVC's cumulative runtime evidence.
        usage = artifacts_dir / "sdo_runtime" / "usage" / "controller-turns.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        usage.write_text(json.dumps({"cwd": f"/wt/{incident_id}", "duration_seconds": 1.0}) + "\n", encoding="utf-8")
        rollout = artifacts_dir / "sdo_runtime" / "codex" / "sessions" / f"rollout-{incident_id}.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        rollout.write_bytes(b'{"type":"session_meta"}\n')
        receipt = _receipt(incident_id)
        receipt["artifacts_dir"] = str(artifacts_dir)
        receipt["completed"] = incident_id not in self.incomplete
        return receipt

    def export_controller_logs(self, control_namespace: str, artifacts_dir: Path) -> None:
        self.events.append(("logs", control_namespace))
        log = artifacts_dir / "sdo_runtime" / "controller_logs" / "sdo-controller-run-1.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(self.controller_logs(control_namespace) + "\n", encoding="utf-8")


def _clock(ops: FakeOps) -> Clock:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return Clock(monotonic=lambda: now[0], sleep=sleep, now=lambda: DETECTED)


def _config(tmp_path: Path, namespace: str = "hotel", stage: str = "s0") -> RuntimeConfig:
    repository = tmp_path / stage / "application_workspace"
    repository.mkdir(parents=True, exist_ok=True)
    return RuntimeConfig(
        repository=repository,
        namespace=namespace,
        application="Hotel Reservation",
        controller_image="controller:test",
        responder_image="responder:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-test",
        timeout_seconds=60,
        repair_policy="recorded-actions",
        controller_namespace=control_namespace_for(namespace),
    )


def _inputs(
    tmp_path: Path,
    stage: str,
    *,
    namespace: str = "hotel",
    application: str = "Hotel Reservation",
    fingerprint: str = "topology-1",
    receipt_dir: Path | None = None,
) -> StageInputs:
    return StageInputs(
        stage_label=stage,
        application=application,
        namespace=namespace,
        lifecycle_fingerprint=fingerprint,
        runtime_config=_config(tmp_path, namespace, stage),
        receipt_dir=receipt_dir or tmp_path / stage / "agent",
        state_path=tmp_path / "sdo_persistent_controller.json",
        verification_timeout_seconds=30,
    )


def _run(
    tmp_path: Path,
    ops: FakeOps,
    stage: str,
    lifecycle_calls: list[str],
    **kwargs: Any,
) -> dict[str, Any]:
    injected: list[str] = []

    def lifecycle() -> bool:
        lifecycle_calls.append(stage)
        return True

    def inject() -> None:
        injected.append(stage)
        ops.events.append(("fault", stage))

    resolution = run_persistent_stage(
        _inputs(tmp_path, stage, **kwargs), ops=ops, run_lifecycle=lifecycle, inject=inject, clock=_clock(ops)
    )
    assert injected == [stage]
    return resolution


def test_control_namespace_is_per_application_and_valid() -> None:
    assert control_namespace_for("hotel-reservation") == "hotel-reservation-sdo"
    with pytest.raises(PersistentControllerError):
        control_namespace_for("x" * 60)


def test_one_controller_serves_both_stages_and_the_second_skips_install_and_lifecycle(tmp_path: Path) -> None:
    ops = FakeOps()
    lifecycle_calls: list[str] = []

    first = _run(tmp_path, ops, "s0", lifecycle_calls)
    ops.reflectable.add("incident-1")
    second = _run(tmp_path, ops, "s1", lifecycle_calls)

    assert ops.installs == 1
    assert lifecycle_calls == ["s0"]
    assert first["persistent_controller"]["controller_pod_uid"] == second["persistent_controller"]["controller_pod_uid"]
    assert first["persistent_controller"]["installed_this_stage"] is True
    assert second["persistent_controller"]["installed_this_stage"] is False
    assert second["persistent_controller"]["lifecycle_revalidation_skipped"] is True
    kinds = [
        event[0] for event in ops.events if event[0] in {"install", "reuse", "active", "paused", "fault", "delete"}
    ]
    assert kinds == ["install", "active", "fault", "paused", "reuse", "active", "fault", "paused"]
    # Each stage correlates its own incident.
    assert (first["incident_id"], second["incident_id"]) == ("incident-1", "incident-2")


def test_stage_reports_resolution_at_verified_health_before_reflection_finishes(tmp_path: Path) -> None:
    ops = FakeOps()

    resolution = _run(tmp_path, ops, "s0", [])

    receipt_dir = tmp_path / "s0" / "agent"
    assert ("reflected", "incident-1") not in ops.events
    assert (receipt_dir / RESOLUTION_FILENAME).is_file()
    assert not (receipt_dir / STRICT_RECEIPT_FILENAME).exists()
    # Detected 12:00:00, verified 12:00:40: reflection is excluded.
    assert resolution["incident_resolution_seconds"] == 40.0
    assert resolution["incident_resolution_scope"] == "detected_to_independently_verified_health"
    assert resolution["confirmed_root_causes"] == [{"summary": "missing ConfigMap"}]
    pending = PersistentState.load(tmp_path / "sdo_persistent_controller.json").controllers["hotel"].pending
    assert pending is not None
    assert pending.incident_id == "incident-1"


def test_next_stage_injects_only_after_previous_reflection_is_committed_and_rolled_out(tmp_path: Path) -> None:
    ops = FakeOps()
    _run(tmp_path, ops, "s0", [])
    ops.reflectable.add("incident-1")
    ops.polls_until_reflected["incident-1"] = 5

    second = _run(tmp_path, ops, "s1", [])

    order = [event[:2] for event in ops.events]
    assert order.index(("reflected", "incident-1")) < order.index(("receipt", "incident-1"))
    assert order.index(("receipt", "incident-1")) < order.index(("fault", "s1"))
    assert second["reflection_drain_seconds"] >= 5.0
    assert (
        second["pre_injection_costs_seconds"]["previous_incident_reflection_drain"]
        == second["reflection_drain_seconds"]
    )
    # The drain is a pre-injection cost, never resolution time.
    assert second["incident_resolution_seconds"] == 40.0
    first_receipt = json.loads((tmp_path / "s0" / "agent" / STRICT_RECEIPT_FILENAME).read_text(encoding="utf-8"))
    assert first_receipt["incident_id"] == "incident-1"
    assert first_receipt["incident_resolution_seconds"] == 40.0
    assert first_receipt["reflection_drain"]["drained_by"] == "s1"
    assert first_receipt["excluded_from_incident_resolution_seconds"]["post_recovery_learning_and_receipt"] == 240.0
    # The controller's repository is synced into the current stage's workspace.
    receipt_event = next(event for event in ops.events if event[0] == "receipt")
    assert receipt_event[2].endswith("s1/application_workspace")


def test_stages_for_different_applications_install_separate_controllers(tmp_path: Path) -> None:
    ops = FakeOps()
    lifecycle_calls: list[str] = []

    first = _run(tmp_path, ops, "s0", lifecycle_calls, namespace="hotel")
    second = _run(tmp_path, ops, "s1", lifecycle_calls, namespace="social", application="Social Network")

    assert ops.installs == 2
    assert lifecycle_calls == ["s0", "s1"]
    assert first["persistent_controller"]["control_namespace"] == "hotel-sdo"
    assert second["persistent_controller"]["control_namespace"] == "social-sdo"
    assert first["persistent_controller"]["controller_pod_uid"] != second["persistent_controller"]["controller_pod_uid"]
    state = PersistentState.load(tmp_path / "sdo_persistent_controller.json")
    assert set(state.controllers) == {"hotel", "social"}


def test_refuses_to_reuse_a_controller_for_a_different_application_in_the_same_namespace(tmp_path: Path) -> None:
    ops = FakeOps()
    _run(tmp_path, ops, "s0", [])

    with pytest.raises(PersistentControllerError, match="refusing to reuse"):
        _run(tmp_path, ops, "s1", [], application="Other App")
    assert ops.installs == 1


def test_a_controller_this_pipeline_did_not_install_is_replaced(tmp_path: Path) -> None:
    ops = FakeOps()
    ops.existing_namespaces.add("hotel-sdo")
    lifecycle_calls: list[str] = []

    _run(tmp_path, ops, "s0", lifecycle_calls)

    assert ops.events[0] == ("delete", "hotel-sdo")
    assert lifecycle_calls == ["s0"]


def test_changed_topology_drains_memory_then_reinstalls(tmp_path: Path) -> None:
    ops = FakeOps()
    lifecycle_calls: list[str] = []
    _run(tmp_path, ops, "s0", lifecycle_calls)
    ops.reflectable.add("incident-1")

    _run(tmp_path, ops, "s1", lifecycle_calls, fingerprint="topology-2")

    order = [event[:2] for event in ops.events]
    assert order.index(("receipt", "incident-1")) < order.index(("delete", "hotel-sdo"))
    assert lifecycle_calls == ["s0", "s1"]
    assert ops.installs == 2


def test_teardown_drains_the_last_incident_and_stops_every_controller(tmp_path: Path) -> None:
    ops = FakeOps()
    _run(tmp_path, ops, "s0", [])
    ops.reflectable.add("incident-1")

    errors = teardown(tmp_path / "sdo_persistent_controller.json", ops=ops, clock=_clock(ops))

    assert errors == []
    assert ("delete", "hotel-sdo") in ops.events
    receipt = json.loads((tmp_path / "s0" / "agent" / STRICT_RECEIPT_FILENAME).read_text(encoding="utf-8"))
    assert receipt["reflection_drain"]["drained_by"] == "pipeline-teardown"
    assert PersistentState.load(tmp_path / "sdo_persistent_controller.json").controllers == {}


def test_deferred_receipts_are_published_into_the_run_the_harness_already_published(tmp_path: Path) -> None:
    # SREGym moves the staging tree to results/<agent>/<problem>/run_N when the
    # stage ends, before the drain can write the strict receipt.
    staging = tmp_path / ".runtime" / "sdo_codex" / "anon_abc"
    ops = FakeOps()
    _run(tmp_path, ops, "s0", [], receipt_dir=staging)
    run_dir = tmp_path / "pipeline" / "stage_0" / "results" / "sdo_codex" / "problem-1" / "run_1"
    run_dir.parent.mkdir(parents=True)
    staging.rename(run_dir)
    ops.reflectable.add("incident-1")
    state_path = tmp_path / "sdo_persistent_controller.json"

    assert teardown(state_path, ops=ops, clock=_clock(ops)) == []
    published = publish_deferred_receipts(state_path, tmp_path / "pipeline")

    receipt_path = run_dir / STRICT_RECEIPT_FILENAME
    assert receipt_path in published
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["incident_id"] == "incident-1"
    # The same opaque-id canonicalization the harness applies at publication.
    assert "anon_abc" not in receipt_path.read_text(encoding="utf-8")
    assert receipt["artifacts_dir"].endswith("problem-1")
    assert (run_dir / "sdo_runtime" / "controller_logs" / "sdo-controller-run-1.log").is_file()
    # Drain-time usage logs and transcripts back per-stage reflection and warm-prompt analysis.
    assert (run_dir / "sdo_runtime" / "usage" / "controller-turns.jsonl").is_file()
    assert (run_dir / "sdo_runtime" / "codex" / "sessions" / "rollout-incident-1.jsonl").read_bytes() == (
        b'{"type":"session_meta"}\n'
    )
    assert not (staging / STRICT_RECEIPT_FILENAME).exists()
    assert not staging.exists()


def test_a_rejected_receipt_is_kept_with_its_validation_error_and_logs(tmp_path: Path) -> None:
    ops = FakeOps()
    _run(tmp_path, ops, "s0", [])
    ops.reflectable.add("incident-1")
    ops.incomplete.add("incident-1")

    errors = teardown(tmp_path / "sdo_persistent_controller.json", ops=ops, clock=_clock(ops))

    assert len(errors) == 1
    assert "completed=true" in errors[0]
    receipt_dir = tmp_path / "s0" / "agent"
    assert not (receipt_dir / STRICT_RECEIPT_FILENAME).exists()
    rejected = json.loads((receipt_dir / REJECTED_RECEIPT_FILENAME).read_text(encoding="utf-8"))
    assert "completed=true" in rejected["validation_error"]
    assert rejected["receipt"]["incident_id"] == "incident-1"
    assert ops.events.count(("logs", "hotel-sdo")) == 2
    assert ("delete", "hotel-sdo") in ops.events
