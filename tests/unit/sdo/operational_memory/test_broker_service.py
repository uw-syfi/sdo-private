from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sdo.agent_runtime.responder.broker_cli import _memory_validator, _production_reflector
from sdo.agent_runtime.responder.reflection import ReflectionTurn, SessionReflector, _classification_directive
from sdo.contracts import DetectorEvaluationStatus, IncidentRequest, IncidentResult
from sdo.operational_memory.broker_service import (
    BrokerClosure,
    BrokerService,
    BrokerServiceError,
    ControllerRolloutRecord,
)
from sdo.operational_memory.commit_broker import CommitBroker
from sdo.operational_memory.models import OutcomeClassification, ValidatorNetworkPolicyCanary
from sdo.operational_memory.repository import MemoryRepository
from sdo.operational_memory.validation import MemoryValidator
from tests.unit.sdo.operational_memory.test_memory import _git, _init_repository, _write_memory


class AcceptRepairValidator:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def validate(self, worktree: Path, changed_paths: list[str]) -> None:
        assert worktree.is_dir()
        self.paths.extend(changed_paths)


class CanaryMemoryValidator(MemoryValidator):
    def __init__(self) -> None:
        super().__init__(run_diagnostics=False)

    def validate(self, *args: object, **kwargs: object) -> tuple[ValidatorNetworkPolicyCanary, ...]:
        super().validate(*args, **kwargs)  # type: ignore[arg-type]
        observed_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        return (
            ValidatorNetworkPolicyCanary(
                mode="allow",
                passed=True,
                job_name="validator-allow",
                observed_at=observed_at,
                details="positive control passed",
            ),
            ValidatorNetworkPolicyCanary(
                mode="deny",
                passed=True,
                job_name="validator-deny",
                observed_at=observed_at,
                details="isolation control passed",
            ),
        )


class CrashOnce:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.crashed = False

    def __call__(self, stage: str) -> None:
        if stage == self.stage and not self.crashed:
            self.crashed = True
            raise RuntimeError(f"simulated crash at {stage}")


class RecordingSessionBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        outcomes = MemoryRepository(worktree).outcomes()
        assert outcomes
        assert outcomes[-1].incident_id in prompt
        assert "Outcome commit:" in prompt
        assert "sharp fault-specific playbook" in prompt
        assert "false-positive refinement" in prompt
        assert "false-negative refinement" in prompt
        assert "originatingIncident" in prompt
        assert "topology fingerprint" in prompt
        playbook = worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
        playbook.write_text(
            playbook.read_text(encoding="utf-8") + "\nRecheck `<TARGET_RESOURCE>` after mitigation.\n",
            encoding="utf-8",
        )
        detector = worktree / ".sdo" / "diagnostics" / "detectors" / "incidents" / "missing_configmap" / "detector.go"
        detector.write_text(
            detector.read_text(encoding="utf-8")
            + "\n// confirmed signature includes a matching and near-miss regression test.\n",
            encoding="utf-8",
        )
        self.calls.append((session_id, idempotency_key))
        return ReflectionTurn(summary="generalized verification", proposed_changes=[str(playbook), str(detector)])


class NoChangeSessionBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        del worktree, prompt
        self.calls.append((session_id, idempotency_key))
        return ReflectionTurn(summary="existing memory is sufficient", proposed_changes=[])


class PlaybookOnlyThenCorrectBackend(RecordingSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        self.attempts += 1
        if self.attempts == 1:
            playbook = worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
            playbook.write_text(
                playbook.read_text(encoding="utf-8") + "\nFirst incomplete attempt.\n",
                encoding="utf-8",
            )
            wrong = worktree / ".sdo" / "diagnostics" / "detectors" / "incident" / "wrong.go"
            wrong.parent.mkdir(parents=True)
            wrong.write_text("package incident\n", encoding="utf-8")
            _git(worktree, "add", ".sdo/playbooks", ".sdo/diagnostics")
            _git(worktree, "commit", "-m", "rejected reflection attempt")
            return ReflectionTurn(summary="incomplete", proposed_changes=[str(playbook), str(wrong)])
        assert "First incomplete attempt." not in (
            worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
        ).read_text(encoding="utf-8")
        assert not (worktree / ".sdo" / "diagnostics" / "detectors" / "incident").exists()
        return super().resume(
            session_id=session_id,
            worktree=worktree,
            prompt=prompt,
            idempotency_key=idempotency_key,
        )


class FailBeforeEditOnceBackend(RecordingSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        if not self.failed:
            self.failed = True
            raise RuntimeError("reflection backend failed before editing")
        return super().resume(
            session_id=session_id,
            worktree=worktree,
            prompt=prompt,
            idempotency_key=idempotency_key,
        )


class PartialEditThenFailBackend(RecordingSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        playbook = worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
        partial_detector = worktree / ".sdo" / "diagnostics" / "detectors" / "incidents" / "partial.go"
        if not self.failed:
            self.failed = True
            playbook.write_text(
                playbook.read_text(encoding="utf-8") + "\nINCOMPLETE REFLECTION\n",
                encoding="utf-8",
            )
            partial_detector.parent.mkdir(parents=True, exist_ok=True)
            partial_detector.write_text("package incidents\n", encoding="utf-8")
            raise RuntimeError("reflection backend failed after partial edits")
        assert "INCOMPLETE REFLECTION" not in playbook.read_text(encoding="utf-8")
        assert not partial_detector.exists()
        return super().resume(
            session_id=session_id,
            worktree=worktree,
            prompt=prompt,
            idempotency_key=idempotency_key,
        )


def test_reflection_policy_has_concrete_false_positive_and_false_negative_refinements() -> None:
    false_positive = _classification_directive(OutcomeClassification.FALSE_POSITIVE)
    false_negative = _classification_directive(OutcomeClassification.FALSE_NEGATIVE)

    assert "tighten" in false_positive
    assert "near-miss" in false_positive
    assert "reproducing test" in false_negative
    assert "add or widen" in false_negative


def _fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[4]
    return (root / "tests" / "fixtures" / "sdo" / "contracts" / name).read_text(encoding="utf-8")


def _closure(worktree: Path, base_commit: str) -> BrokerClosure:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json")).model_copy(
        update={"repository_worktree": str(worktree), "repository_base_commit": base_commit}
    )
    result = IncidentResult.model_validate_json(_fixture("incident_result.json"))
    return BrokerClosure(
        request=request,
        result=result,
        final_detector_states=result.final_detector_states,
        detected_at=datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc),
        dispatched_at=datetime(2026, 7, 9, 18, 1, tzinfo=timezone.utc),
        responder_completed_at=datetime(2026, 7, 9, 18, 5, tzinfo=timezone.utc),
        verified_at=datetime(2026, 7, 9, 18, 6, tzinfo=timezone.utc),
    )


def _service(target: Path, worktrees: Path, validator: AcceptRepairValidator, **kwargs: object) -> BrokerService:
    broker = CommitBroker(
        target,
        validator=MemoryValidator(run_diagnostics=False),
        proposal_validator=validator,
    )
    return BrokerService(target, worktrees, broker=broker, responder_model="gpt-5", **kwargs)


def test_production_broker_configures_same_session_reflector() -> None:
    reflector = _production_reflector(
        executable="codex-custom",
        model="gpt-5",
        reasoning_effort="high",
        timeout_seconds=321,
    )

    assert reflector.backend.executable == "codex-custom"
    assert reflector.backend.model == "gpt-5"
    assert reflector.backend.reasoning_effort == "high"
    assert reflector.backend.timeout_seconds == 321


def test_in_cluster_broker_uses_isolated_kubernetes_validator_job() -> None:
    validator = _memory_validator(
        "kubernetes",
        namespace="demo",
        image="sdo-detector-validator:test",
        repository_pvc="application-repository",
        repository_mount_path=Path("/workspace"),
    )

    sandbox = validator.sandbox_runner
    assert sandbox.__class__.__name__ == "KubernetesJobSandboxRunner"
    assert sandbox.namespace == "demo"
    assert sandbox.image == "sdo-detector-validator:test"
    assert sandbox.repository_pvc == "application-repository"
    assert sandbox.timeout_seconds == 600


def test_closure_persists_validator_network_policy_canaries_in_durable_ledger(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    broker = CommitBroker(
        target,
        validator=CanaryMemoryValidator(),
        proposal_validator=AcceptRepairValidator(),
    )
    service = BrokerService(target, worktrees, broker=broker)
    workspace = service.prepare_incident("inc-20260709-0001")

    service.process_closure(_closure(workspace.path, workspace.base_commit))
    state = service.completion_state("inc-20260709-0001")

    assert state.validator_evidence_commit == state.outcome_commit
    assert [canary.mode for canary in state.validator_network_policy_canaries] == ["allow", "deny"]
    reloaded = service._load("inc-20260709-0001")
    assert reloaded is not None
    assert reloaded.validator_network_policy_canaries == state.validator_network_policy_canaries


def test_recorded_actions_closure_skips_empty_repair_commit_and_persists_actions(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    service = _service(target, worktrees, validator, repair_policy="recorded-actions")
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)
    closure = closure.model_copy(
        update={"request": closure.request.model_copy(update={"repair_policy": "recorded-actions"})}
    )

    receipt = service.process_closure(closure)

    assert receipt.proposal_commit is None
    assert "SDO-Phase: proposal" not in _git(target, "log", "--format=%B")
    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.repair_commit is None
    assert outcome.repair_actions == closure.result.repair_actions


def test_recorded_actions_policy_still_commits_source_changes(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    service = _service(target, worktrees, validator, repair_policy="recorded-actions")
    workspace = service.prepare_incident("inc-20260709-0001")
    (workspace.path / "application.txt").write_text("repaired\n", encoding="utf-8")
    closure = _closure(workspace.path, workspace.base_commit)
    closure = closure.model_copy(
        update={"request": closure.request.model_copy(update={"repair_policy": "recorded-actions"})}
    )

    receipt = service.process_closure(closure)

    assert receipt.proposal_commit is not None
    assert (target / "application.txt").read_text(encoding="utf-8") == "repaired\n"
    assert validator.paths == ["application.txt"]


def test_recorded_actions_policy_brokers_responder_committed_source_changes(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    (target / "application.txt").write_text("before\n", encoding="utf-8")
    _git(target, "add", "application.txt")
    _git(target, "commit", "-m", "application source")
    validator = AcceptRepairValidator()
    service = _service(target, worktrees, validator, repair_policy="recorded-actions")
    workspace = service.prepare_incident("inc-20260709-0001")
    (workspace.path / "application.txt").write_text("after\n", encoding="utf-8")
    _git(workspace.path, "add", "application.txt")
    _git(workspace.path, "commit", "-m", "responder repair")
    closure = _closure(workspace.path, workspace.base_commit)
    closure = closure.model_copy(
        update={"request": closure.request.model_copy(update={"repair_policy": "recorded-actions"})}
    )

    receipt = service.process_closure(closure)

    assert receipt.proposal_commit is not None
    assert (target / "application.txt").read_text(encoding="utf-8") == "after\n"
    assert validator.paths == ["application.txt"]


def test_recorded_actions_closure_rejects_confirmed_repair_without_action_or_commit(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    service = _service(target, worktrees, AcceptRepairValidator(), repair_policy="recorded-actions")
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)
    assert closure.result is not None
    closure = closure.model_copy(
        update={
            "request": closure.request.model_copy(update={"repair_policy": "recorded-actions"}),
            "result": closure.result.model_copy(update={"repair_actions": []}),
        }
    )

    with pytest.raises(BrokerServiceError, match="successful recorded repair action"):
        service.process_closure(closure)


@pytest.mark.parametrize(
    "crash_stage",
    ["proposal_commit_unrecorded", "outcome_commit_unrecorded", "ack_durable_before_cleanup"],
)
def test_closure_pipeline_recovers_each_crash_point_exactly_once(tmp_path: Path, crash_stage: str) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    (target / "application.txt").write_text("before\n", encoding="utf-8")
    _git(target, "add", "application.txt")
    _git(target, "commit", "-m", "application source")
    validator = AcceptRepairValidator()
    crash = CrashOnce(crash_stage)
    service = _service(target, worktrees, validator, checkpoint=crash)
    incident_id = "inc-20260709-0001"

    workspace = service.prepare_incident(incident_id)
    assert service.prepare_incident(incident_id) == workspace
    (workspace.path / "application.txt").write_text("after\n", encoding="utf-8")
    closure = _closure(workspace.path, workspace.base_commit)
    assert (target / "application.txt").read_text(encoding="utf-8") == "before\n"

    if crash_stage == "ack_durable_before_cleanup":
        receipt = service.process_closure(closure)
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.acknowledge(receipt)
    else:
        with pytest.raises(RuntimeError, match="simulated crash"):
            service.process_closure(closure)

    restarted = _service(target, worktrees, validator)
    receipt = restarted.process_closure(closure)
    assert restarted.process_closure(closure) == receipt
    assert workspace.path.exists()
    restarted.acknowledge(receipt)
    restarted.acknowledge(receipt)

    assert not workspace.path.exists()
    assert (target / "application.txt").read_text(encoding="utf-8") == "after\n"
    assert validator.paths == ["application.txt"]
    assert _git(target, "log", "--format=%B").count("SDO-Phase: proposal") == 1
    assert _git(target, "log", "--format=%B").count("SDO-Phase: outcome") == 1
    outcomes = MemoryRepository(target).outcomes()
    assert [outcome.incident_id for outcome in outcomes].count(incident_id) == 1
    assert outcomes[-1].repair_commit == receipt.proposal_commit
    assert outcomes[-1].memory_commit == receipt.proposal_commit


def test_verified_outcome_resumes_same_session_after_outcome_commit_and_recovers_reflection_commit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = RecordingSessionBackend()
    reflector = SessionReflector(backend)
    crash = CrashOnce("reflection_commit_unrecorded")
    service = _service(target, worktrees, validator, checkpoint=crash, reflector=reflector)
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.process_closure(closure)

    restarted = _service(target, worktrees, validator, reflector=reflector)
    receipt = restarted.process_closure(closure)

    assert receipt.proposal_commit is not None
    assert receipt.reflection_commit is not None
    assert backend.calls == [("019c-session-0001", f"reflection:inc-20260709-0001:{receipt.outcome_commit}")]
    messages = _git(target, "log", "--reverse", "--format=%B")
    assert messages.index("SDO-Phase: proposal") < messages.index("SDO-Phase: outcome")
    assert messages.index("SDO-Phase: outcome") < messages.index("SDO-Phase: reflection")
    state = restarted.completion_state("inc-20260709-0001")
    assert state.topology_reviewed_at_commit == receipt.outcome_commit
    assert state.source_topology_fingerprint
    assert state.architecture_topology_fingerprint == "topology-1"
    assert state.stale_memory_detected is True
    assert state.accepted_detector_paths == [".sdo/diagnostics/detectors/incidents/missing_configmap/detector.go"]
    assert state.controller_update_required is True
    restarted.acknowledge(receipt)
    assert not workspace.path.exists()


def test_verified_no_change_reflection_gets_an_attributed_empty_commit(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = NoChangeSessionBackend()
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        reflector=SessionReflector(backend),
    )
    workspace = service.prepare_incident("inc-20260709-0001")

    receipt = service.process_closure(_closure(workspace.path, workspace.base_commit))

    assert receipt.reflection_commit is not None
    assert "SDO-Phase: reflection" in _git(target, "show", "-s", "--format=%B", receipt.reflection_commit)
    assert backend.calls == [("019c-session-0001", f"reflection:inc-20260709-0001:{receipt.outcome_commit}")]


def test_semantically_incomplete_reflection_is_rolled_back_and_retried(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = PlaybookOnlyThenCorrectBackend()
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        reflector=SessionReflector(backend),
    )
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(BrokerServiceError, match="sharp fault-specific detector"):
        service.process_closure(closure)

    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_backend_completed is False
    assert state.reflection_validation_error
    receipt = service.process_closure(closure)
    assert receipt.reflection_commit is not None
    assert backend.attempts == 2


def test_bounded_invalid_reflection_falls_back_to_attributed_noop(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = PlaybookOnlyThenCorrectBackend()
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        reflector=SessionReflector(backend),
        max_reflection_attempts=1,
    )
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(BrokerServiceError, match="sharp fault-specific detector"):
        service.process_closure(closure)

    receipt = service.process_closure(closure)
    state = service.completion_state("inc-20260709-0001")
    assert receipt.reflection_commit is not None
    assert state.reflection_attempts == 1
    assert state.reflection_validation_error
    assert state.reflection_proposed_changes
    assert backend.attempts == 1


def test_controller_rollout_evidence_is_correlated_atomic_and_restart_durable(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    receipt = service.process_closure(_closure(workspace.path, workspace.base_commit))
    assert receipt.reflection_commit is not None

    expectation = service.pending_controller_rollout(receipt.reflection_commit)
    assert expectation is not None
    assert expectation.incident_id == "inc-20260709-0001"
    assert expectation.reflection_commit == receipt.reflection_commit
    assert expectation.before_detector_fingerprint
    assert expectation.after_detector_fingerprint
    assert expectation.before_detector_fingerprint != expectation.after_detector_fingerprint

    started = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    failed = ControllerRolloutRecord(
        incident_id=expectation.incident_id,
        reflection_commit=expectation.reflection_commit,
        before_detector_fingerprint=expectation.before_detector_fingerprint,
        after_detector_fingerprint=expectation.after_detector_fingerprint,
        controller_job="sdo-controller-run",
        controller_pod_uid="failed-pod-uid",
        started_at=started,
        completed_at=started,
        returncode=17,
        success=False,
    )
    service.record_controller_rollout(failed)

    restarted = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    assert restarted.pending_controller_rollout(receipt.reflection_commit) == expectation
    successful = failed.model_copy(update={"controller_pod_uid": "success-pod-uid", "returncode": 0, "success": True})
    restarted.record_controller_rollout(successful)
    assert restarted.pending_controller_rollout(receipt.reflection_commit) is None
    state = restarted.completion_state(expectation.incident_id)
    assert state.controller_update_rollouts == [failed, successful]
    assert not list(restarted.state_root.glob("*.tmp"))


def test_controller_rollout_ledger_rejects_mismatch_and_duplicate_success(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    service = _service(
        target,
        worktrees,
        validator,
        reflector=SessionReflector(RecordingSessionBackend()),
    )
    workspace = service.prepare_incident("inc-20260709-0001")
    receipt = service.process_closure(_closure(workspace.path, workspace.base_commit))
    assert receipt.reflection_commit is not None
    expectation = service.pending_controller_rollout(receipt.reflection_commit)
    assert expectation is not None
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    valid = ControllerRolloutRecord(
        incident_id=expectation.incident_id,
        reflection_commit=expectation.reflection_commit,
        before_detector_fingerprint=expectation.before_detector_fingerprint,
        after_detector_fingerprint=expectation.after_detector_fingerprint,
        controller_job="sdo-controller-run",
        controller_pod_uid="pod-1",
        started_at=now,
        completed_at=now,
        returncode=0,
        success=True,
    )

    with pytest.raises(BrokerServiceError, match="reflection commit"):
        service.record_controller_rollout(valid.model_copy(update={"reflection_commit": "wrong"}))
    with pytest.raises(BrokerServiceError, match="detector transition"):
        service.record_controller_rollout(valid.model_copy(update={"before_detector_fingerprint": "wrong"}))
    service.record_controller_rollout(valid)
    with pytest.raises(BrokerServiceError, match="successful rollout"):
        service.record_controller_rollout(valid.model_copy(update={"controller_pod_uid": "pod-2"}))


def test_failed_or_unverified_outcome_never_reflects_as_success(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)
    closure = closure.model_copy(
        update={
            "final_detector_states": [
                closure.final_detector_states[0].model_copy(update={"status": DetectorEvaluationStatus.FIRING})
            ]
        }
    )

    receipt = service.process_closure(closure)

    assert receipt.reflection_commit is None
    assert backend.calls == []


def test_reflection_retries_after_backend_failure_before_any_edit(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = FailBeforeEditOnceBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(RuntimeError, match="failed before editing"):
        service.process_closure(closure)

    restarted = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    receipt = restarted.recover("inc-20260709-0001")

    assert receipt.reflection_commit is not None
    assert backend.calls == [("019c-session-0001", f"reflection:inc-20260709-0001:{receipt.outcome_commit}")]
    state = restarted.completion_state("inc-20260709-0001")
    assert state.closure == closure
    assert state.responder_session_id == "019c-session-0001"
    assert state.reflection_backend_completed is True


def test_recovery_rolls_back_partial_reflection_edits_before_resuming_same_session(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = PartialEditThenFailBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(RuntimeError, match="failed after partial edits"):
        service.process_closure(closure)

    restarted = _service(target, worktrees, validator, reflector=SessionReflector(backend))
    receipt = restarted.recover("inc-20260709-0001")
    restarted.acknowledge(receipt)
    state = restarted.completion_state("inc-20260709-0001")

    assert receipt.reflection_commit is not None
    assert state.acknowledged is True
    assert state.cleaned is True
    assert not workspace.path.exists()
    assert backend.calls == [("019c-session-0001", f"reflection:inc-20260709-0001:{receipt.outcome_commit}")]


def test_mitigation_time_memory_edits_are_rejected_before_health_verification(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    service = _service(target, worktrees, validator)
    workspace = service.prepare_incident("inc-20260709-0001")
    playbook = workspace.path / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
    playbook.write_text(playbook.read_text(encoding="utf-8") + "\nPremature learning.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="before independent health verification"):
        service.process_closure(_closure(workspace.path, workspace.base_commit))

    assert _git(target, "log", "--format=%B").count("SDO-Phase: proposal") == 0
