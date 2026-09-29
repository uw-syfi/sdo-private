from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sdo.agent_runtime.responder.broker_cli import _memory_validator, _production_reflector
from sdo.agent_runtime.responder.reflection import ReflectionTurn, SessionReflector, _classification_directive
from sdo.contracts import (
    DetectorEvaluation,
    DetectorEvaluationStatus,
    IncidentRequest,
    IncidentResult,
    ObjectRef,
    PriorOutcomeEvidence,
    RootCauseEvidence,
)
from sdo.operational_memory.broker_service import (
    BrokerClosure,
    BrokerService,
    BrokerServiceError,
    ClosureReceipt,
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
        return self._reflect(session_id=session_id, worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)

    def fresh(self, *, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        return self._reflect(session_id="fresh", worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)

    def _reflect(
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
        return ReflectionTurn(
            summary="generalized verification",
            learning_decision="updated",
            proposed_changes=[str(playbook), str(detector)],
        )


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
        return ReflectionTurn(
            summary="existing memory is sufficient",
            learning_decision="no_change",
            no_change_reason="the existing detector and playbook already encode this exact verified signature",
            proposed_changes=[],
        )


class PlaybookOnlyThenCorrectBackend(RecordingSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0
        self.fresh_prompts: list[str] = []

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        self.attempts += 1
        self.calls.append((session_id, idempotency_key))
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
            return ReflectionTurn(
                summary="incomplete",
                learning_decision="updated",
                proposed_changes=[str(playbook), str(wrong)],
            )
        raise AssertionError("a retry after validation failure must not resume the responder session")

    def fresh(self, *, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        self.attempts += 1
        self.fresh_prompts.append(prompt)
        assert "First incomplete attempt." not in (
            worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
        ).read_text(encoding="utf-8")
        assert not (worktree / ".sdo" / "diagnostics" / "detectors" / "incident").exists()
        return super().fresh(worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)


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


def test_closure_accepts_the_controllers_cleaned_helper_record(tmp_path: Path) -> None:
    payload = _closure(tmp_path, "0" * 40).model_dump(mode="json")
    payload["cleaned_helpers"] = ["Pod/hotel-reservation/curl-debug"]

    assert BrokerClosure.model_validate(payload).cleaned_helpers == ["Pod/hotel-reservation/curl-debug"]
    assert BrokerClosure.model_validate(_closure(tmp_path, "0" * 40).model_dump()).cleaned_helpers == []


def _unlearned_closure(worktree: Path, base_commit: str) -> BrokerClosure:
    """A closure whose finding came from no registered incident detector."""

    closure = _closure(worktree, base_commit)
    findings = [finding.model_copy(update={"detector_id": "health-objective"}) for finding in closure.request.findings]
    return closure.model_copy(update={"request": closure.request.model_copy(update={"findings": findings})})


def _service(target: Path, worktrees: Path, validator: AcceptRepairValidator, **kwargs: object) -> BrokerService:
    broker = CommitBroker(
        target,
        validator=MemoryValidator(run_diagnostics=False),
        proposal_validator=validator,
    )
    # Most tests script a resumed responder session; the default is asserted separately.
    kwargs.setdefault("reflection_session", "resume")
    return BrokerService(target, worktrees, broker=broker, responder_model="gpt-5", **kwargs)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_production_broker_configures_same_session_reflector(provider: str) -> None:
    reflector = _production_reflector(
        provider=provider,
        model="gpt-5",
        reasoning_effort="high",
        timeout_seconds=321,
    )

    assert reflector.backend.provider == provider
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


def _recorded_actions_closure(
    worktree: Path, base_commit: str, incident_id: str = "inc-20260709-0001"
) -> BrokerClosure:
    closure = _closure(worktree, base_commit)
    assert closure.result is not None
    request = closure.request.model_copy(update={"incident_id": incident_id, "repair_policy": "recorded-actions"})
    result = closure.result.model_copy(update={"incident_id": incident_id})
    return closure.model_copy(update={"request": request, "result": result})


def test_recorded_actions_healed_stray_closes_as_cancelled_and_does_not_learn(tmp_path: Path) -> None:
    """N13 / F16: a completed result with no action, but health already clear, is a no-op.

    A real responder that finds an already-healed transient (for example a stray 3 s
    data-plane stall) truthfully reports this shape. It must close cleanly instead of
    being rejected forever, and it must not be credited as a mitigation or learned from.
    """

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = MustNotReflectBackend()
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        repair_policy="recorded-actions",
        reflector=SessionReflector(backend),
    )
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _recorded_actions_closure(workspace.path, workspace.base_commit)
    closure = closure.model_copy(update={"result": closure.result.model_copy(update={"repair_actions": []})})

    receipt = service.process_closure(closure)
    service.acknowledge(receipt)

    assert backend.calls == []
    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.classification == OutcomeClassification.CANCELLED
    assert outcome.repair_commit is None
    assert "SDO-Phase: proposal" not in _git(target, "log", "--format=%B")


def test_recorded_actions_healed_stray_does_not_block_the_next_incident(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    service = _service(target, worktrees, AcceptRepairValidator(), repair_policy="recorded-actions")

    stray_workspace = service.prepare_incident("inc-20260709-0001")
    stray_closure = _recorded_actions_closure(stray_workspace.path, stray_workspace.base_commit)
    stray_closure = stray_closure.model_copy(
        update={"result": stray_closure.result.model_copy(update={"repair_actions": []})}
    )
    stray_receipt = service.process_closure(stray_closure)
    service.acknowledge(stray_receipt)

    next_workspace = service.prepare_incident("inc-20260709-0002")
    next_closure = _recorded_actions_closure(next_workspace.path, next_workspace.base_commit, "inc-20260709-0002")

    next_receipt = service.process_closure(next_closure)

    assert next_receipt.outcome_commit is not None
    outcomes = MemoryRepository(target).outcomes()
    assert [outcome.incident_id for outcome in outcomes] == ["inc-20260709-0001", "inc-20260709-0002"]
    assert outcomes[0].classification == OutcomeClassification.CANCELLED
    assert outcomes[1].classification == OutcomeClassification.SUCCESS


def test_recorded_actions_closure_rejects_completed_claim_while_still_unhealthy(tmp_path: Path) -> None:
    """A responder claiming success without acting while health is still bad must fail loudly."""

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    service = _service(target, worktrees, AcceptRepairValidator(), repair_policy="recorded-actions")
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _recorded_actions_closure(workspace.path, workspace.base_commit)
    still_firing = closure.final_detector_states[0].model_copy(update={"status": DetectorEvaluationStatus.FIRING})
    closure = closure.model_copy(
        update={
            "result": closure.result.model_copy(update={"repair_actions": []}),
            "final_detector_states": [still_firing],
        }
    )
    assert not closure.health_verified

    with pytest.raises(BrokerServiceError, match="not verified clear"):
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
    closure = _unlearned_closure(workspace.path, workspace.base_commit)

    with pytest.raises(BrokerServiceError, match="sharp fault-specific detector"):
        service.process_closure(closure)

    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_backend_completed is False
    assert state.reflection_validation_error
    assert state.reflection_fresh_retry_attempts == 0
    receipt = service.process_closure(closure)
    assert receipt.reflection_commit is not None
    assert backend.attempts == 2
    # Attempt 1 resumed the responder session; the retry was a short fresh
    # session carrying only the rejected diff, the validator error and the
    # original structured request.
    assert backend.calls[0][0] == "019c-session-0001"
    assert backend.calls[1][0] == "fresh"
    (retry_prompt,) = backend.fresh_prompts
    assert state.reflection_validation_error in retry_prompt
    assert "First incomplete attempt." in retry_prompt
    assert ".sdo/diagnostics/detectors/incident/wrong.go" in retry_prompt
    assert "Outcome commit:" in retry_prompt
    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_attempts == 2
    assert state.reflection_fresh_retry_attempts == 1


def test_bounded_invalid_reflection_records_explicit_no_change_and_allows_closure(tmp_path: Path) -> None:
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
    closure = _unlearned_closure(workspace.path, workspace.base_commit)

    with pytest.raises(BrokerServiceError, match="sharp fault-specific detector"):
        service.process_closure(closure)

    receipt = service.process_closure(closure)
    state = service.completion_state("inc-20260709-0001")
    assert receipt.reflection_commit is not None
    assert state.reflection_commit == receipt.reflection_commit
    assert state.reflection_completed is True
    assert state.reflection_attempts == 1
    assert state.reflection_validation_error
    assert state.reflection_learning_decision == "no_change"
    assert state.reflection_no_change_reason
    assert "failed independent validation" in state.reflection_no_change_reason
    assert state.reflection_proposed_changes == []
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


def test_health_that_cleared_only_after_detector_review_is_not_a_responder_success(tmp_path: Path) -> None:
    """A responder that claimed a wrong fix must not be credited when health recovers after its window."""

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    payload = _closure(workspace.path, workspace.base_commit).model_dump(mode="json")
    payload["detector_review_required_at"] = "2026-07-09T18:05:30Z"
    payload["detector_review_reason"] = "health detectors did not clear within 2m0s after responder completion"
    closure = BrokerClosure.model_validate(payload)

    receipt = service.process_closure(closure)

    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.classification == OutcomeClassification.PARTIAL
    assert outcome.timestamps.verified_at is None
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


class AlwaysFailingBackend(RecordingSessionBackend):
    def resume(self, *, session_id: str, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        del worktree, prompt
        self.calls.append((session_id, idempotency_key))
        raise RuntimeError("codex exited with status 1")


def test_a_reflection_backend_that_always_fails_is_bounded_and_the_closure_completes(tmp_path: Path) -> None:
    """Unbounded backend failures spent the controller's closure retries and wedged a persistent controller."""

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = AlwaysFailingBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend), max_reflection_attempts=2)
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="status 1"):
            service.process_closure(closure)
    receipt = service.process_closure(closure)

    assert len(backend.calls) == 2
    assert receipt.reflection_commit is not None
    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_attempts == 2
    assert state.reflection_learning_decision == "no_change"
    assert "codex exited with status 1" in str(state.reflection_no_change_reason)
    assert "failed independent validation" not in str(state.reflection_no_change_reason)


class KilledMidTurnBackend(RecordingSessionBackend):
    """A reflection turn the broker process never finishes: for example a
    SIGKILL during `resume()`. SystemExit is not caught by `except Exception`,
    so it models a process death that runs no Python cleanup at all, only
    whatever the broker already made durable before the call."""

    def __init__(self) -> None:
        super().__init__()
        self.calls_started = 0

    def resume(self, *, session_id: str, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        del session_id, worktree, prompt, idempotency_key
        self.calls_started += 1
        raise SystemExit("broker process killed mid-reflection")


def test_broker_killed_mid_reflection_counts_the_attempt(tmp_path: Path) -> None:
    """A broker killed while a reflection turn is in flight must still count

    the attempt, so repeated kills cannot retry the same incident forever.
    """

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    validator = AcceptRepairValidator()
    backend = KilledMidTurnBackend()
    service = _service(target, worktrees, validator, reflector=SessionReflector(backend), max_reflection_attempts=2)
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _closure(workspace.path, workspace.base_commit)

    with pytest.raises(SystemExit):
        service.process_closure(closure)
    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_attempts == 1, "a broker kill mid-turn must still count as an attempt"

    # A broker restart is a fresh process reading the same persisted ledger.
    restarted = _service(target, worktrees, validator, reflector=SessionReflector(backend), max_reflection_attempts=2)
    with pytest.raises(SystemExit):
        restarted.recover("inc-20260709-0001")
    state = restarted.completion_state("inc-20260709-0001")
    assert state.reflection_attempts == 2, "a second broker kill must also count, bounding the retries"
    assert backend.calls_started == 2

    # The retry budget is spent: a third restart must not call the backend
    # again, and must complete the closure without learning.
    final_service = _service(
        target, worktrees, validator, reflector=SessionReflector(backend), max_reflection_attempts=2
    )
    receipt = final_service.recover("inc-20260709-0001")

    assert backend.calls_started == 2
    assert receipt.reflection_commit is not None
    final_state = final_service.completion_state("inc-20260709-0001")
    assert final_state.reflection_learning_decision == "no_change"


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


class MeteredNoChangeBackend(NoChangeSessionBackend):
    def resume(self, **kwargs: object) -> ReflectionTurn:  # type: ignore[override]
        turn = super().resume(**kwargs)  # type: ignore[arg-type]
        return turn.with_usage({"llm_calls": 1, "input_tokens": 1200, "output_tokens": 80})


def test_reflection_token_usage_is_recorded_in_the_durable_ledger(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        reflector=SessionReflector(MeteredNoChangeBackend()),
    )
    workspace = service.prepare_incident("inc-20260709-0001")

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_usage == {"llm_calls": 1, "input_tokens": 1200, "output_tokens": 80}


def test_session_backend_attaches_provider_usage_to_reflection_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentshim import ProviderUsage, TokenUsage

    from libs.agent_cli.structured import StructuredTurn
    from sdo.agent_runtime.responder import reflection

    def fake_turn(*_args: object, **_kwargs: object) -> StructuredTurn:
        return StructuredTurn(
            output_json='{"summary": "s", "learning_decision": "no_change", "no_change_reason": "r", '
            '"proposed_changes": []}',
            session_id="session",
            usage=ProviderUsage(tokens=TokenUsage(input_tokens=10, output_tokens=2, cached_input_tokens=4, turns=1)),
        )

    monkeypatch.setattr(reflection, "run_structured_turn", fake_turn)

    turn = reflection.CodexSessionBackend(model="m").resume(
        session_id="session", worktree=Path("."), prompt="p", idempotency_key="k"
    )

    assert turn.usage["input_tokens"] == 10
    assert turn.usage["cached_input_tokens"] == 4
    assert turn.usage["llm_calls"] == 1


def test_reflection_output_schema_is_strict_structured_output_compatible() -> None:
    """OpenAI strict schemas reject optional properties: every key must be required."""
    from sdo.agent_runtime.responder.reflection import reflection_output_schema

    schema = reflection_output_schema()

    assert sorted(schema["required"]) == sorted(schema["properties"])
    assert schema["additionalProperties"] is False
    assert all("default" not in prop for prop in schema["properties"].values())
    updated = ReflectionTurn.model_validate(
        {"summary": "s", "learning_decision": "updated", "no_change_reason": None, "proposed_changes": ["p"]}
    )
    assert updated.no_change_reason is None


def test_session_backend_requests_the_strict_reflection_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentshim import ProviderUsage

    from libs.agent_cli.structured import StructuredTurn
    from sdo.agent_runtime.responder import reflection

    seen: dict[str, object] = {}

    def fake_turn(*_args: object, **kwargs: object) -> StructuredTurn:
        seen["schema"] = kwargs["output_schema"]
        return StructuredTurn(
            output_json='{"summary": "s", "learning_decision": "no_change", "no_change_reason": "r", '
            '"proposed_changes": []}',
            session_id="session",
            usage=ProviderUsage(),
        )

    monkeypatch.setattr(reflection, "run_structured_turn", fake_turn)
    reflection.CodexSessionBackend(model="m").resume(
        session_id="session", worktree=Path("."), prompt="p", idempotency_key="k"
    )

    assert seen["schema"] == reflection.reflection_output_schema()


class PromptCapturingNoChangeBackend(NoChangeSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def resume(self, *, session_id: str, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        self.prompts.append(prompt)
        return super().resume(session_id=session_id, worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)


def test_reflection_prompt_carries_the_broker_computed_topology_review(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = PromptCapturingNoChangeBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    state = service.completion_state("inc-20260709-0001")
    (prompt,) = backend.prompts
    assert state.source_topology_fingerprint
    assert f"arch.md topology fingerprint: {state.architecture_topology_fingerprint}" in prompt
    assert f"current source topology fingerprint: {state.source_topology_fingerprint}" in prompt
    assert "stale_memory_detected: true" in prompt


class MustNotReflectBackend(RecordingSessionBackend):
    def _reflect(self, **_kwargs: object) -> ReflectionTurn:  # type: ignore[override]
        raise AssertionError("a repeated exact-match success must not run an LLM reflection turn")


def _exact_match_outcome(match_reason: str = "exact-fingerprint") -> PriorOutcomeEvidence:
    return PriorOutcomeEvidence(
        incident_id="inc-20260701-0001",
        match_reason=match_reason,  # type: ignore[arg-type]
        root_cause_summaries=["geo referenced an absent required ConfigMap"],
        repair_action_summaries=["restored geo-config"],
        applied_playbooks=[".sdo/playbooks/missing-configmap/README.md"],
        source_commit="1111111111111111111111111111111111111111",
        exact_source_match=True,
    )


def _own_playbook(target: Path) -> None:
    """Register the fixture playbook in its incident detector's possiblePlaybooks."""

    manifest = target / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "possiblePlaybooks: []", "possiblePlaybooks: [.sdo/playbooks/missing-configmap/README.md]"
        ),
        encoding="utf-8",
    )


def _warm_closure(
    worktree: Path,
    base_commit: str,
    *,
    match_reason: str = "exact-fingerprint",
    incident_status: DetectorEvaluationStatus | None = DetectorEvaluationStatus.CLEAR,
    repair_success: bool = True,
    applied_playbook: bool = True,
    incident_detector_fired: bool = True,
) -> BrokerClosure:
    closure = _closure(worktree, base_commit)
    request = closure.request.model_copy(update={"relevant_outcomes": [_exact_match_outcome(match_reason)]})
    if not incident_detector_fired:
        # Only the health detector fired at dispatch; the learned incident detector had not.
        findings = [finding.model_copy(update={"detector_id": "health-objective"}) for finding in request.findings]
        request = request.model_copy(update={"findings": findings, "surfaced_playbooks": []})
    assert closure.result is not None
    actions = [action.model_copy(update={"success": repair_success}) for action in closure.result.repair_actions]
    result = closure.result.model_copy(
        update={
            "repair_actions": actions,
            "applied_playbooks": closure.result.applied_playbooks if applied_playbook else [],
        }
    )
    incident_states = (
        []
        if incident_status is None
        else [
            DetectorEvaluation(
                detector_id="missing-configmap",
                evaluated_at=datetime(2026, 7, 9, 18, 5, 30, tzinfo=timezone.utc),
                status=incident_status,
                fingerprints=[] if incident_status == DetectorEvaluationStatus.CLEAR else ["hotel-reservation/geo"],
            )
        ]
    )
    return closure.model_copy(
        update={
            "request": request,
            "result": result,
            "incident_detector_states": incident_states,
        }
    )


@pytest.mark.parametrize(
    ("variant", "expected_state"),
    [
        ({}, "missing-configmap clear after the response"),
        ({"incident_status": None}, "missing-configmap not evaluated after the response"),
        (
            {"incident_detector_fired": False, "incident_status": None},
            "missing-configmap not evaluated after the response",
        ),
    ],
)
def test_repeated_exact_match_success_records_deterministic_noop_reflection(
    tmp_path: Path, variant: dict[str, object], expected_state: str
) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _own_playbook(target)
    _init_repository(target)
    diagnostics_before = _git(target, "rev-parse", "HEAD:.sdo/diagnostics")
    backend = MustNotReflectBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    receipt = service.process_closure(
        _warm_closure(workspace.path, workspace.base_commit, **variant)  # type: ignore[arg-type]
    )

    state = service.completion_state("inc-20260709-0001")
    assert backend.calls == []
    assert state.reflection_attempts == 0
    assert state.reflection_usage == {}
    assert state.reflection_completed is True
    reason = state.reflection_skipped_reason
    assert reason is not None
    assert reason.startswith("repeated exact-match success: prior verified outcome(s) inc-20260701-0001")
    assert ".sdo/playbooks/missing-configmap/README.md (detector missing-configmap" in reason
    fired = variant.get("incident_detector_fired", True)
    assert ("fired at dispatch" if fired else "had not fired at dispatch") in reason
    assert "the controller verified health" in reason
    assert expected_state in reason
    assert state.reflection_learning_decision == "no_change"
    assert state.reflection_no_change_reason == reason
    assert state.accepted_detector_paths == []
    assert state.controller_update_required is False
    assert receipt.reflection_commit is not None
    assert "SDO-Phase: reflection" in _git(target, "show", "-s", "--format=%B", receipt.reflection_commit)
    assert _git(target, "rev-parse", "HEAD:.sdo/diagnostics") == diagnostics_before
    assert MemoryRepository(target).outcomes()[-1].classification == OutcomeClassification.SUCCESS


@pytest.mark.parametrize(
    "variant",
    [
        {"match_reason": "detector-rule-resource-kind"},
        {"incident_status": DetectorEvaluationStatus.FIRING},
        {"incident_detector_fired": False, "incident_status": DetectorEvaluationStatus.FIRING},
        {"applied_playbook": False},
        {"owned": False},
    ],
)
def test_non_exact_or_unproven_warm_success_still_runs_full_reflection(
    tmp_path: Path, variant: dict[str, object]
) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    variant = dict(variant)
    if variant.pop("owned", True):
        _own_playbook(target)
    _init_repository(target)
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    service.process_closure(_warm_closure(workspace.path, workspace.base_commit, **variant))  # type: ignore[arg-type]

    state = service.completion_state("inc-20260709-0001")
    assert len(backend.calls) == 1
    assert state.reflection_skipped_reason is None


def test_a_warm_success_whose_every_repair_failed_is_an_unlearned_external_recovery(tmp_path: Path) -> None:
    """No successful repair action backs the cause, so health recovered for another reason (F8)."""

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _own_playbook(target)
    _init_repository(target)
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    receipt = service.process_closure(_warm_closure(workspace.path, workspace.base_commit, repair_success=False))

    state = service.completion_state("inc-20260709-0001")
    assert backend.calls == []
    assert state.reflection_skipped_reason is None
    assert receipt.reflection_commit is None
    assert MemoryRepository(target).outcomes()[-1].classification == OutcomeClassification.EXTERNAL_RECOVERY


class PlaybookOnlyBackend(RecordingSessionBackend):
    def _reflect(self, *, session_id: str, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        del prompt
        playbook = worktree / ".sdo" / "playbooks" / "missing-configmap" / "README.md"
        playbook.write_text(
            playbook.read_text(encoding="utf-8") + "\nRun `kubectl -n <NAMESPACE> get configmap geo-config`.\n",
            encoding="utf-8",
        )
        self.calls.append((session_id, idempotency_key))
        return ReflectionTurn(
            summary="sharper verification", learning_decision="updated", proposed_changes=[str(playbook)]
        )


def test_playbook_only_reflection_is_accepted_when_a_learned_incident_detector_fired(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = PlaybookOnlyBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    receipt = service.process_closure(_closure(workspace.path, workspace.base_commit))

    state = service.completion_state("inc-20260709-0001")
    assert len(backend.calls) == 1
    assert receipt.reflection_commit is not None
    assert state.accepted_detector_paths == []
    assert state.controller_update_required is False
    assert "get configmap geo-config" in (target / ".sdo" / "playbooks" / "missing-configmap" / "README.md").read_text(
        encoding="utf-8"
    )


class FreshFirstAttemptBackend(RecordingSessionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.fresh_prompts: list[str] = []

    def resume(self, **_kwargs: object) -> ReflectionTurn:  # type: ignore[override]
        raise AssertionError("fresh reflection mode must not resume the responder session")

    def fresh(self, *, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        self.fresh_prompts.append(prompt)
        return super().fresh(worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)


def test_fresh_reflection_mode_starts_the_first_attempt_in_a_fresh_session(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = FreshFirstAttemptBackend()
    service = _service(
        target,
        worktrees,
        AcceptRepairValidator(),
        reflector=SessionReflector(backend),
        reflection_session="fresh",
    )
    workspace = service.prepare_incident("inc-20260709-0001")

    receipt = service.process_closure(_closure(workspace.path, workspace.base_commit))

    assert receipt.reflection_commit is not None
    assert backend.calls == [("fresh", f"reflection:inc-20260709-0001:{receipt.outcome_commit}")]
    (prompt,) = backend.fresh_prompts
    # The brief carries the closure's live evidence and the responder's result.
    assert "Deployment hotel-reservation/geo requires ConfigMap geo-config" in prompt
    assert "The geo Deployment referenced an absent required ConfigMap" in prompt
    state = service.completion_state("inc-20260709-0001")
    assert state.reflection_session_mode == "fresh"
    assert state.reflection_attempts == 1
    # A fresh first attempt is not a validation retry.
    assert state.reflection_fresh_retry_attempts == 0


def test_reflection_session_mode_defaults_to_fresh(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    broker = CommitBroker(target, validator=MemoryValidator(run_diagnostics=False))

    service = BrokerService(target, tmp_path / "worktrees", broker=broker, responder_model="gpt-5")

    assert service.reflection_session == "fresh"


def test_explicit_resume_mode_resumes_the_responder_session_and_is_recorded(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = NoChangeSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")

    service.process_closure(_closure(workspace.path, workspace.base_commit))

    assert backend.calls[0][0] == "019c-session-0001"
    assert service.completion_state("inc-20260709-0001").reflection_session_mode == "resume"


def test_reflection_session_mode_is_validated(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)

    with pytest.raises(ValueError, match="reflection session"):
        _service(target, tmp_path / "worktrees", AcceptRepairValidator(), reflection_session="transcript")


def test_ledgers_written_before_reflection_modes_parse_without_a_mode() -> None:
    from sdo.operational_memory.broker_service import BrokerLedger

    ledger = BrokerLedger.model_validate({"incident_id": "inc", "worktree": "/w", "base_commit": "b"})

    assert ledger.reflection_session_mode is None


def test_broker_cli_reflection_session_defaults_to_fresh() -> None:
    from sdo.agent_runtime.responder.broker_cli import _argument_parser

    required = ["--repository", "/repo", "--worktree-root", "/worktrees"]
    parser = _argument_parser()

    assert parser.parse_args(required).reflection_session == "fresh"
    assert parser.parse_args([*required, "--reflection-session", "resume"]).reflection_session == "resume"
    with pytest.raises(SystemExit):
        parser.parse_args([*required, "--reflection-session", "transcript"])


# F8: a cause is learned only when the responder's own repair backs it.


class AlwaysReflectReflector(SessionReflector):
    """A reflector that would reflect on anything; the broker must still refuse to learn."""

    def should_reflect(self, outcome: object, *, health_verified: bool, session_id: str | None) -> bool:
        del outcome, health_verified, session_id
        return True


def _attribution_closure(worktree: Path, base_commit: str, *, cause: str, repaired: str) -> BrokerClosure:
    """A verified closure whose dispatch diff was fully reverted before verification.

    ``cause`` is the ``Kind/name`` the responder blamed and ``repaired`` the one its action touched.
    """

    closure = _closure(worktree, base_commit)
    state_changes = IncidentRequest.model_validate_json(_fixture("incident_request_state_changes.json")).state_changes
    assert state_changes is not None
    assert closure.result is not None
    cause_kind, cause_name = cause.split("/")
    repaired_kind, repaired_name = repaired.split("/")
    result = closure.result
    root_cause = result.confirmed_root_causes[0].model_copy(
        update={
            "summary": f"{cause} caused the incident",
            "resources": [ObjectRef(kind=cause_kind, namespace="hotel-reservation", name=cause_name)],
            "evidence": [RootCauseEvidence(kind="live-observation", source=f"kubectl get {cause}", observation="bad")],
        }
    )
    action = result.repair_actions[0].model_copy(
        update={
            "target": repaired,
            "resources": [ObjectRef(kind=repaired_kind, namespace="hotel-reservation", name=repaired_name)],
            "started_at": datetime(2026, 7, 9, 18, 2, tzinfo=timezone.utc),
            "completed_at": datetime(2026, 7, 9, 18, 3, tzinfo=timezone.utc),
        }
    )
    cleared = datetime(2026, 7, 9, 18, 5, 30, tzinfo=timezone.utc)
    return closure.model_copy(
        update={
            "request": closure.request.model_copy(update={"state_changes": state_changes}),
            "result": result.model_copy(update={"confirmed_root_causes": [root_cause], "repair_actions": [action]}),
            "final_state_changes": state_changes.model_copy(update={"changes": []}),
            "health_cleared_at": cleared,
        }
    )


@pytest.mark.parametrize("reflector_type", [SessionReflector, AlwaysReflectReflector])
def test_a_wrong_cause_fixed_by_someone_else_is_never_learned(
    tmp_path: Path, reflector_type: type[SessionReflector]
) -> None:
    """F8: the responder blamed frontend and restarted it; someone else reverted the real fault."""

    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=reflector_type(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _attribution_closure(
        workspace.path, workspace.base_commit, cause="Deployment/frontend", repaired="Deployment/frontend"
    )

    receipt = service.process_closure(closure)

    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.classification == OutcomeClassification.EXTERNAL_RECOVERY
    assert [verification.verdict.value for verification in outcome.diagnosis_verification] == ["unattributed"]
    assert receipt.reflection_commit is None
    assert backend.calls == []
    assert service.completion_state("inc-20260709-0001").accepted_detector_paths == []


def test_a_correct_cause_backed_by_its_own_repair_is_confirmed_and_learned(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktrees = tmp_path / "worktrees"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    backend = RecordingSessionBackend()
    service = _service(target, worktrees, AcceptRepairValidator(), reflector=SessionReflector(backend))
    workspace = service.prepare_incident("inc-20260709-0001")
    closure = _attribution_closure(
        workspace.path, workspace.base_commit, cause="ConfigMap/geo-config", repaired="ConfigMap/geo-config"
    )

    receipt = service.process_closure(closure)

    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.classification == OutcomeClassification.SUCCESS
    assert [verification.verdict.value for verification in outcome.diagnosis_verification] == ["confirmed"]
    assert receipt.reflection_commit is not None
    assert len(backend.calls) == 1


# Cross-language: the broker processes closures the Go controller wrote.

_GO_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "contracts" / "go"


@pytest.mark.parametrize(
    ("fixture", "classification", "verdict"),
    [
        ("closure_repaired.json", OutcomeClassification.SUCCESS, "confirmed"),
        ("closure_own_edit.json", OutcomeClassification.SUCCESS, "contradicted"),
    ],
)
def test_the_broker_processes_a_go_encoded_closure_into_a_ledger_and_outcome(
    tmp_path: Path, fixture: str, classification: OutcomeClassification, verdict: str
) -> None:
    """The Go closure, as the broker CLI receives it, commits an outcome with the F17 facts applied."""

    target = tmp_path / "target"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    service = _service(target, tmp_path / "worktrees", AcceptRepairValidator(), repair_policy="recorded-actions")
    payload = json.loads((_GO_FIXTURES / fixture).read_text(encoding="utf-8"))
    workspace = service.prepare_incident(payload["request"]["incident_id"])
    payload["request"]["repository_worktree"] = str(workspace.path)
    payload["request"]["repository_base_commit"] = workspace.base_commit

    receipt = service.process_closure(BrokerClosure.model_validate(payload))

    ledger = service.completion_state(payload["request"]["incident_id"])
    assert ledger.outcome_commit == receipt.outcome_commit
    assert ClosureReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    outcome = MemoryRepository(target).outcomes()[-1]
    assert outcome.classification == classification
    assert [item.verdict.value for item in outcome.diagnosis_verification] == [verdict]
    repair = outcome.diagnosis_verification[0].repair
    assert repair is not None
    assert repair.attributed is True
