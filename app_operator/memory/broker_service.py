from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app_operator.memory.commit_broker import CommitBroker, CommitBrokerError
from app_operator.memory.models import (
    ArtifactOwner,
    OutcomeClassification,
    OutcomeRecord,
    ValidatorNetworkPolicyCanary,
)
from app_operator.memory.outcomes import OutcomeFacts, derive_outcome
from app_operator.memory.repository import MemoryRepository
from app_operator.memory.validation import MemoryValidationError
from app_operator.memory.worktrees import IncidentWorktree, WorktreeManager
from app_operator.protocol.models import (  # noqa: TC001 - Pydantic resolves these annotations at runtime.
    DetectorEvaluation,
    IncidentRequest,
    IncidentResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app_operator.responder.session import SessionReflector


class BrokerServiceError(RuntimeError):
    """Raised when durable incident brokerage cannot advance safely."""


class BrokerClosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: IncidentRequest
    result: IncidentResult | None = None
    dispatch_error: str | None = None
    final_detector_states: list[DetectorEvaluation] = Field(default_factory=list)
    detected_at: datetime
    dispatched_at: datetime
    responder_completed_at: datetime
    verified_at: datetime


class ClosureReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    worktree: str
    base_commit: str
    proposal_commit: str | None = None
    outcome_commit: str
    reflection_commit: str | None = None
    ack_token: str


class ControllerRolloutExpectation(BaseModel):
    """The exact accepted detector transition a controller must activate."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1)
    reflection_commit: str = Field(min_length=1)
    before_detector_fingerprint: str = Field(min_length=1)
    after_detector_fingerprint: str = Field(min_length=1)


class ControllerRolloutRecord(BaseModel):
    """Durable evidence for one controller rollout attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["sdo.controller-rollout/v1"] = "sdo.controller-rollout/v1"
    incident_id: str = Field(min_length=1)
    reflection_commit: str = Field(min_length=1)
    before_detector_fingerprint: str = Field(min_length=1)
    after_detector_fingerprint: str = Field(min_length=1)
    controller_job: str = Field(min_length=1)
    controller_pod_uid: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    returncode: StrictInt
    success: bool

    @model_validator(mode="after")
    def validate_attempt(self) -> ControllerRolloutRecord:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.success != (self.returncode == 0):
            raise ValueError("success must be true exactly when returncode is zero")
        return self


class BrokerLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    worktree: str
    base_commit: str
    closure: BrokerClosure | None = None
    responder_session_id: str | None = None
    proposal_commit: str | None = None
    outcome_commit: str | None = None
    reflection_started: bool = False
    reflection_backend_completed: bool = False
    reflection_summary: str | None = None
    reflection_proposed_changes: list[str] = Field(default_factory=list)
    reflection_validation_error: str | None = None
    reflection_completed: bool = False
    reflection_commit: str | None = None
    topology_reviewed_at_commit: str | None = None
    architecture_topology_fingerprint: str | None = None
    source_topology_fingerprint: str | None = None
    stale_memory_detected: bool = False
    accepted_detector_paths: list[str] = Field(default_factory=list)
    controller_update_required: bool = False
    # Optional/defaulted fields are the explicit v1 migration path for ledgers
    # written before durable rollout evidence was introduced.
    controller_update_before_fingerprint: str | None = None
    controller_update_after_fingerprint: str | None = None
    controller_update_rollouts: list[ControllerRolloutRecord] = Field(default_factory=list)
    validator_evidence_commit: str | None = None
    validator_network_policy_canaries: list[ValidatorNetworkPolicyCanary] = Field(default_factory=list)
    ack_token: str | None = None
    acknowledged: bool = False
    cleaned: bool = False


class BrokerService:
    def __init__(
        self,
        target_repository: Path,
        worktree_root: Path,
        *,
        broker: CommitBroker | None = None,
        responder_backend: str = "codex",
        responder_model: str = "unknown",
        checkpoint: Callable[[str], None] | None = None,
        reflector: SessionReflector | None = None,
    ) -> None:
        self.target_repository = target_repository.resolve()
        self.worktrees = WorktreeManager(self.target_repository, worktree_root)
        self.broker = broker or CommitBroker(self.target_repository)
        self.responder_backend = responder_backend
        self.responder_model = responder_model
        self.checkpoint = checkpoint or (lambda _stage: None)
        self.reflector = reflector
        common_dir = Path(self.broker._git(self.target_repository, "rev-parse", "--git-common-dir").strip())
        if not common_dir.is_absolute():
            common_dir = self.target_repository / common_dir
        self.state_root = common_dir.resolve() / "sdo-broker"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.state_root / "service.lock"

    def prepare_incident(self, incident_id: str) -> IncidentWorktree:
        with self._locked():
            ledger = self._load(incident_id)
            if ledger is not None:
                return IncidentWorktree(
                    incident_id=incident_id,
                    path=Path(ledger.worktree),
                    base_commit=ledger.base_commit,
                )
            workspace = self.worktrees.prepare(incident_id)
            self._save(
                BrokerLedger(
                    incident_id=incident_id,
                    worktree=str(workspace.path),
                    base_commit=workspace.base_commit,
                )
            )
            return workspace

    def process_closure(self, closure: BrokerClosure) -> ClosureReceipt:
        incident_id = closure.request.incident_id
        with self._locked():
            ledger = self._required_ledger(incident_id)
            self._validate_closure_workspace(closure, ledger)
            if ledger.closure is None:
                ledger.closure = closure
                ledger.responder_session_id = None if closure.result is None else closure.result.responder_session_id
                self._save(ledger)
            else:
                closure = ledger.closure
            ledger = self._recover_commits(ledger)
            worktree = Path(ledger.worktree)

            if ledger.proposal_commit is None and ledger.outcome_commit is None:
                changed_paths = self.broker.changed_paths(worktree)
                proposal = self.broker.commit_proposal(
                    incident_worktree=worktree,
                    incident_id=incident_id,
                    allow_empty=not changed_paths,
                )
                self.checkpoint("proposal_commit_unrecorded")
                ledger.proposal_commit = proposal.commit_sha
                self._save(ledger)

            if ledger.outcome_commit is None:
                repository = MemoryRepository(worktree)
                if not any(record.incident_id == incident_id for record in repository.outcomes()):
                    repository.append_outcome(
                        self._outcome(closure, ledger),
                        actor=ArtifactOwner.CONTROLLER,
                    )
                outcome_commit = self.broker.commit(
                    incident_worktree=worktree,
                    incident_id=incident_id,
                    actor=ArtifactOwner.CONTROLLER,
                    phase="outcome",
                )
                self.checkpoint("outcome_commit_unrecorded")
                ledger.outcome_commit = outcome_commit.commit_sha
                ledger.validator_evidence_commit = outcome_commit.commit_sha
                ledger.validator_network_policy_canaries = list(outcome_commit.validator_network_policy_canaries)
                self._save(ledger)

            if ledger.outcome_commit is None:
                raise BrokerServiceError("closure processing did not produce an outcome commit")
            ledger = self._process_reflection(closure, ledger)
            ledger = self._ensure_validator_evidence(ledger)
            ledger.ack_token = self._ack_token(ledger)
            self._save(ledger)
            return self._receipt(ledger)

    def recover(self, incident_id: str) -> ClosureReceipt:
        with self._locked():
            ledger = self._required_ledger(incident_id)
            closure = ledger.closure
        if closure is None:
            raise BrokerServiceError(f"incident {incident_id!r} has no persisted closure to recover")
        return self.process_closure(closure)

    def completion_state(self, incident_id: str) -> BrokerLedger:
        with self._locked():
            return self._required_ledger(incident_id).model_copy(deep=True)

    def pending_controller_rollout(self, reflection_commit: str) -> ControllerRolloutExpectation | None:
        """Resolve one pending rollout by reflection commit, never file recency."""

        with self._locked():
            candidates: list[BrokerLedger] = []
            for path in self.state_root.glob("*.json"):
                ledger = BrokerLedger.model_validate_json(path.read_text(encoding="utf-8"))
                if ledger.controller_update_required and ledger.reflection_commit == reflection_commit:
                    candidates.append(ledger)
            if not candidates:
                return None
            if len(candidates) != 1:
                raise BrokerServiceError(
                    f"multiple incidents require controller rollout for reflection commit {reflection_commit}"
                )
            ledger = candidates[0]
            ledger = self._ensure_controller_update_transition(ledger)
            expectation = self._rollout_expectation(ledger)
            successful = 0
            for record in ledger.controller_update_rollouts:
                self._validate_rollout_correlation(record, expectation)
                if record.success:
                    successful += 1
            if successful > 1:
                raise BrokerServiceError("incident ledger contains multiple successful rollout records")
            return None if successful == 1 else expectation

    def record_controller_rollout(self, record: ControllerRolloutRecord) -> None:
        """Atomically append one incident-correlated rollout attempt."""

        with self._locked():
            ledger = self._required_ledger(record.incident_id)
            ledger = self._ensure_controller_update_transition(ledger)
            expectation = self._rollout_expectation(ledger)
            self._validate_rollout_correlation(record, expectation)
            if record.success and any(existing.success for existing in ledger.controller_update_rollouts):
                raise BrokerServiceError("incident ledger already contains a successful rollout record")
            ledger.controller_update_rollouts.append(record)
            self._save(ledger)

    def acknowledge(self, receipt: ClosureReceipt) -> None:
        with self._locked():
            ledger = self._required_ledger(receipt.incident_id)
            if ledger.outcome_commit != receipt.outcome_commit or self._ack_token(ledger) != receipt.ack_token:
                raise BrokerServiceError("closure receipt does not match durable broker state")
            if not ledger.acknowledged:
                ledger.acknowledged = True
                ledger.ack_token = receipt.ack_token
                self._save(ledger)
                self.checkpoint("ack_durable_before_cleanup")
            if not ledger.cleaned:
                self.worktrees.cleanup(receipt.incident_id)
                ledger.cleaned = True
                self._save(ledger)

    def _outcome(self, closure: BrokerClosure, ledger: BrokerLedger) -> OutcomeRecord:
        result = closure.result
        health_verified = bool(closure.final_detector_states) and all(
            evaluation.status.value == "clear" for evaluation in closure.final_detector_states
        )
        surfaced = [playbook.path for playbook in closure.request.surfaced_playbooks]
        applied = [] if result is None else [playbook.path for playbook in result.applied_playbooks]
        return derive_outcome(
            OutcomeFacts(
                request=closure.request,
                result=result,
                dispatch_error=closure.dispatch_error,
                final_health_detector_state=closure.final_detector_states,
                health_verified=health_verified,
                fault_confirmed=bool(result and result.confirmed_root_causes),
                inspected_playbooks=surfaced,
                confirmed_playbooks=applied,
                rejected_playbooks=sorted(set(surfaced) - set(applied)),
                repair_commit=ledger.proposal_commit,
                memory_commit=ledger.proposal_commit,
                responder_backend=self.responder_backend,
                responder_model=self.responder_model,
                detected_at=closure.detected_at,
                dispatched_at=closure.dispatched_at,
                responder_completed_at=closure.responder_completed_at,
                verified_at=closure.verified_at if health_verified else None,
            )
        )

    def _recover_commits(self, ledger: BrokerLedger) -> BrokerLedger:
        changed = False
        if ledger.proposal_commit is None:
            proposal = self.broker.find_attributed_commit(ledger.incident_id, "proposal")
            if proposal is not None:
                ledger.proposal_commit = proposal
                changed = True
        if ledger.outcome_commit is None:
            outcome = self.broker.find_attributed_commit(ledger.incident_id, "outcome")
            if outcome is not None:
                ledger.outcome_commit = outcome
                changed = True
        if ledger.reflection_commit is None:
            reflection = self.broker.find_attributed_commit(ledger.incident_id, "reflection")
            if reflection is not None:
                ledger.reflection_commit = reflection
                ledger.reflection_completed = True
                changed_paths = self.broker._git(
                    self.target_repository,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    reflection,
                ).splitlines()
                ledger.accepted_detector_paths = sorted(
                    path
                    for path in changed_paths
                    if path.startswith(".sdo/diagnostics/detectors/incidents/")
                    or path == ".sdo/diagnostics/manifest.yaml"
                )
                ledger.controller_update_required = bool(ledger.accepted_detector_paths)
                ledger = self._ensure_controller_update_transition(ledger)
                changed = True
        if changed:
            self._save(ledger)
        return ledger

    def _process_reflection(self, closure: BrokerClosure, ledger: BrokerLedger) -> BrokerLedger:
        if self.reflector is None or ledger.reflection_completed:
            return ledger
        worktree = Path(ledger.worktree)
        repository = MemoryRepository(worktree)
        outcomes = repository.outcomes()
        outcome = next(record for record in reversed(outcomes) if record.incident_id == ledger.incident_id)
        if ledger.topology_reviewed_at_commit != ledger.outcome_commit:
            architecture_fingerprint = repository.architecture().metadata.topology_fingerprint
            source_fingerprint = self._source_topology_fingerprint(worktree)
            ledger.topology_reviewed_at_commit = ledger.outcome_commit
            ledger.architecture_topology_fingerprint = architecture_fingerprint
            ledger.source_topology_fingerprint = source_fingerprint
            ledger.stale_memory_detected = architecture_fingerprint != source_fingerprint
            self._save(ledger)
        session_id = None if closure.result is None else closure.result.responder_session_id
        health_verified = bool(closure.final_detector_states) and all(
            evaluation.status.value == "clear" for evaluation in closure.final_detector_states
        )
        if not self.reflector.should_reflect(outcome, health_verified=health_verified, session_id=session_id):
            ledger.reflection_completed = True
            self._save(ledger)
            return ledger
        changed_paths = self.broker.changed_paths(worktree)
        if ledger.reflection_started and not ledger.reflection_backend_completed:
            if changed_paths:
                self._rollback_incomplete_reflection(worktree)
                changed_paths = []
            ledger.reflection_started = False
            self._save(ledger)
        if not ledger.reflection_backend_completed:
            ledger.reflection_started = True
            self._save(ledger)
            turn = self.reflector.resume(
                session_id=session_id or "",
                incident_id=ledger.incident_id,
                worktree=worktree,
                outcome=outcome,
                history=outcomes,
                outcome_commit=ledger.outcome_commit or "",
                validation_feedback=ledger.reflection_validation_error,
            )
            ledger.reflection_backend_completed = True
            ledger.reflection_summary = turn.summary
            ledger.reflection_proposed_changes = list(turn.proposed_changes)
            self._save(ledger)
            self.checkpoint("reflection_resumed_unrecorded")
            changed_paths = self.broker.changed_paths(worktree)
        if changed_paths:
            detector_paths = sorted(
                path
                for path in changed_paths
                if path.startswith(".sdo/diagnostics/detectors/incidents/") or path == ".sdo/diagnostics/manifest.yaml"
            )
            if (
                outcome.classification == OutcomeClassification.SUCCESS
                and outcome.confirmed_root_causes
                and not detector_paths
            ):
                raise BrokerServiceError(
                    "successful confirmed incident reflection must include a sharp fault-specific detector update"
                )
            try:
                reflection = self.broker.commit_proposal(
                    incident_worktree=worktree,
                    incident_id=ledger.incident_id,
                    phase="reflection",
                )
            except (CommitBrokerError, MemoryValidationError) as exc:
                ledger.reflection_backend_completed = False
                ledger.reflection_validation_error = str(exc)
                self._save(ledger)
                raise BrokerServiceError(
                    f"reflection proposal failed isolated validation and must be regenerated: {exc}"
                ) from exc
            self.checkpoint("reflection_commit_unrecorded")
            ledger.reflection_commit = reflection.commit_sha
            ledger.validator_evidence_commit = reflection.commit_sha
            ledger.validator_network_policy_canaries = list(reflection.validator_network_policy_canaries)
            ledger.accepted_detector_paths = detector_paths
            ledger.controller_update_required = bool(detector_paths)
            ledger = self._ensure_controller_update_transition(ledger)
            ledger.reflection_validation_error = None
        ledger.reflection_completed = True
        self._save(ledger)
        return ledger

    def _ensure_validator_evidence(self, ledger: BrokerLedger) -> BrokerLedger:
        """Recover evidence lost after a validated commit but before ledger persistence."""
        evidence_commit = ledger.reflection_commit or ledger.outcome_commit
        if evidence_commit is None or ledger.validator_evidence_commit == evidence_commit:
            return ledger
        changed_paths = self.broker._git(
            self.target_repository,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            evidence_commit,
        ).splitlines()
        memory_paths = sorted(path for path in changed_paths if path.startswith(".sdo/"))
        if not memory_paths:
            raise BrokerServiceError("validated memory commit has no operational-memory paths")
        actor = ArtifactOwner.RESPONDER if ledger.reflection_commit else ArtifactOwner.CONTROLLER
        evidence = self.broker.validate_memory_state(
            Path(ledger.worktree),
            actor=actor,
            changed_paths=memory_paths,
        )
        ledger.validator_evidence_commit = evidence_commit
        ledger.validator_network_policy_canaries = list(evidence)
        self._save(ledger)
        return ledger

    def _source_topology_fingerprint(self, root: Path) -> str:
        digest = hashlib.sha256()
        tracked = self.broker._git(root, "ls-files", "-z").split("\0")
        for relative in sorted(path for path in tracked if path and not path.startswith(".sdo/")):
            path = root / relative
            if not path.is_file():
                continue
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _ensure_controller_update_transition(self, ledger: BrokerLedger) -> BrokerLedger:
        if not ledger.controller_update_required:
            return ledger
        if ledger.outcome_commit is None or ledger.reflection_commit is None:
            raise BrokerServiceError("controller update requires outcome and reflection commits")
        before = self._diagnostics_tree_fingerprint(ledger.outcome_commit)
        after = self._diagnostics_tree_fingerprint(ledger.reflection_commit)
        if before == after:
            raise BrokerServiceError("accepted detector update did not change the diagnostics tree")
        if ledger.controller_update_before_fingerprint not in (None, before):
            raise BrokerServiceError("durable controller rollout before fingerprint is inconsistent")
        if ledger.controller_update_after_fingerprint not in (None, after):
            raise BrokerServiceError("durable controller rollout after fingerprint is inconsistent")
        changed = (
            ledger.controller_update_before_fingerprint is None or ledger.controller_update_after_fingerprint is None
        )
        ledger.controller_update_before_fingerprint = before
        ledger.controller_update_after_fingerprint = after
        if changed:
            self._save(ledger)
        return ledger

    def _diagnostics_tree_fingerprint(self, commit: str) -> str:
        for relative in (".sdo/diagnostics", ".sds/diagnostics"):
            try:
                value = self.broker._git(self.target_repository, "rev-parse", f"{commit}:{relative}").strip()
            except CommitBrokerError:
                continue
            if value:
                return value
        raise BrokerServiceError(f"commit {commit} has no diagnostics tree")

    @staticmethod
    def _rollout_expectation(ledger: BrokerLedger) -> ControllerRolloutExpectation:
        if (
            ledger.reflection_commit is None
            or ledger.controller_update_before_fingerprint is None
            or ledger.controller_update_after_fingerprint is None
        ):
            raise BrokerServiceError("controller rollout transition is incomplete")
        return ControllerRolloutExpectation(
            incident_id=ledger.incident_id,
            reflection_commit=ledger.reflection_commit,
            before_detector_fingerprint=ledger.controller_update_before_fingerprint,
            after_detector_fingerprint=ledger.controller_update_after_fingerprint,
        )

    @staticmethod
    def _validate_rollout_correlation(
        record: ControllerRolloutRecord,
        expectation: ControllerRolloutExpectation,
    ) -> None:
        if record.incident_id != expectation.incident_id:
            raise BrokerServiceError("controller rollout incident ID does not match durable ledger")
        if record.reflection_commit != expectation.reflection_commit:
            raise BrokerServiceError("controller rollout reflection commit does not match durable ledger")
        if (
            record.before_detector_fingerprint != expectation.before_detector_fingerprint
            or record.after_detector_fingerprint != expectation.after_detector_fingerprint
        ):
            raise BrokerServiceError("controller rollout detector transition does not match durable ledger")

    def _rollback_incomplete_reflection(self, worktree: Path) -> None:
        self.broker._git(worktree, "reset", "--hard", "HEAD")
        self.broker._git(
            worktree,
            "clean",
            "-fd",
            "--",
            ".sdo/playbooks",
            ".sdo/diagnostics/detectors/incidents",
        )

    @staticmethod
    def _validate_closure_workspace(closure: BrokerClosure, ledger: BrokerLedger) -> None:
        if Path(closure.request.repository_worktree).resolve() != Path(ledger.worktree).resolve():
            raise BrokerServiceError("closure worktree does not match prepared incident worktree")
        if closure.request.repository_base_commit != ledger.base_commit:
            raise BrokerServiceError("closure base commit does not match prepared incident base")

    @staticmethod
    def _ack_token(ledger: BrokerLedger) -> str:
        if ledger.outcome_commit is None:
            raise BrokerServiceError("cannot acknowledge closure without outcome commit")
        return hashlib.sha256(
            f"{ledger.incident_id}\0{ledger.outcome_commit}\0{ledger.reflection_commit or ''}".encode()
        ).hexdigest()

    @staticmethod
    def _receipt(ledger: BrokerLedger) -> ClosureReceipt:
        if ledger.outcome_commit is None or ledger.ack_token is None:
            raise BrokerServiceError("broker ledger has no completed closure receipt")
        return ClosureReceipt(
            incident_id=ledger.incident_id,
            worktree=ledger.worktree,
            base_commit=ledger.base_commit,
            proposal_commit=ledger.proposal_commit,
            outcome_commit=ledger.outcome_commit,
            reflection_commit=ledger.reflection_commit,
            ack_token=ledger.ack_token,
        )

    def _required_ledger(self, incident_id: str) -> BrokerLedger:
        ledger = self._load(incident_id)
        if ledger is None:
            raise BrokerServiceError(f"incident {incident_id!r} has no prepared worktree")
        return ledger

    def _load(self, incident_id: str) -> BrokerLedger | None:
        path = self._ledger_path(incident_id)
        if not path.is_file():
            return None
        return BrokerLedger.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, ledger: BrokerLedger) -> None:
        path = self._ledger_path(ledger.incident_id)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(ledger.model_dump_json(indent=2))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _ledger_path(self, incident_id: str) -> Path:
        digest = hashlib.sha256(incident_id.encode()).hexdigest()
        return self.state_root / f"{digest}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
