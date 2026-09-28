from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from sdo.contracts import (
    DetectorEvaluation,
    DetectorEvaluationStatus,
    IncidentRequest,
    IncidentResult,
    StateChanges,
)
from sdo.operational_memory.commit_broker import CommitBroker, CommitBrokerError
from sdo.operational_memory.models import (
    ArtifactOwner,
    OutcomeClassification,
    OutcomeRecord,
    ValidatorNetworkPolicyCanary,
)
from sdo.operational_memory.outcomes import OutcomeFacts, derive_outcome
from sdo.operational_memory.repository import MemoryRepository, MemoryRepositoryError
from sdo.operational_memory.validation import MemoryValidationError
from sdo.operational_memory.warm_path import warm_playbook_matches
from sdo.operational_memory.worktrees import IncidentWorktree, WorktreeManager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

#: Recoveries the responder is not credited with: health that cleared only
#: after the verification window (F4), or that the responder's own repair
#: does not back (F8). The broker never reflects on them.
_UNCREDITED_RECOVERIES = frozenset({OutcomeClassification.PARTIAL, OutcomeClassification.EXTERNAL_RECOVERY})


class BrokerServiceError(RuntimeError):
    """Raised when durable incident brokerage cannot advance safely."""


#: How the first reflection attempt runs: ``resume`` continues the responder's
#: own session (the default, same-session design); ``fresh`` starts a new
#: session from a compact broker-built incident brief. Validation retries are
#: always fresh.
ReflectionSessionMode = Literal["resume", "fresh"]
REFLECTION_SESSION_MODES: tuple[ReflectionSessionMode, ...] = ("resume", "fresh")


class ReflectionProposal(Protocol):
    summary: str
    learning_decision: Literal["updated", "no_change"]
    no_change_reason: str | None
    proposed_changes: list[str]

    @property
    def usage(self) -> Mapping[str, int | float]: ...


class TopologyReview(BaseModel):
    """The broker's own comparison of arch.md's topology with the current source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    architecture_topology_fingerprint: str = Field(min_length=1)
    source_topology_fingerprint: str = Field(min_length=1)
    stale_memory_detected: bool


class OutcomeReflector(Protocol):
    """Learns from one verified incident outcome.

    A call without ``validation_feedback`` is the first attempt. With
    ``session_mode="resume"`` it resumes the responder's own session
    (``session_id``); with ``"fresh"`` it starts a new session from a compact
    brief built from ``closure``. A call with feedback retries a proposal the
    broker rejected; it runs in a short fresh session that sees only
    ``rejected_proposal_diff``, the feedback and the original request.
    """

    def should_reflect(
        self,
        outcome: OutcomeRecord,
        *,
        health_verified: bool,
        session_id: str | None,
    ) -> bool: ...

    def resume(
        self,
        *,
        session_id: str,
        incident_id: str,
        worktree: Path,
        outcome: OutcomeRecord,
        history: list[OutcomeRecord],
        outcome_commit: str,
        validation_feedback: str | None = None,
        topology_review: TopologyReview | None = None,
        rejected_proposal_diff: str | None = None,
        session_mode: ReflectionSessionMode = "resume",
        closure: BrokerClosure | None = None,
    ) -> ReflectionProposal: ...


class BrokerClosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: IncidentRequest
    result: IncidentResult | None = None
    dispatch_error: str | None = None
    final_detector_states: list[DetectorEvaluation] = Field(default_factory=list)
    # Latest post-response evaluation of each non-health detector that raised a
    # finding; learning evidence only, never a closure gate.
    incident_detector_states: list[DetectorEvaluation] = Field(default_factory=list)
    # The controller's configuration diff against the healthy baseline as of
    # verification time (N11); can name a composite's later fault the
    # dispatch-time request diff missed. None without a baseline.
    final_state_changes: StateChanges | None = None
    detected_at: datetime
    dispatched_at: datetime
    responder_completed_at: datetime
    verified_at: datetime
    # When the health detectors began their final clear streak (F8): only a
    # repair action that started by then can back a root cause. None from
    # controllers that predate it.
    health_cleared_at: datetime | None = None
    # Responder helper objects the controller deleted, as Kind/namespace/name.
    cleaned_helpers: list[str] = Field(default_factory=list)
    # Set when health did not clear within the controller's verification
    # window after the responder completed; later recovery is not credited to it.
    detector_review_required_at: datetime | None = None
    detector_review_reason: str | None = None

    @property
    def health_verified(self) -> bool:
        """Every final health detector is clear, within the responder's verification window."""

        return (
            self.detector_review_required_at is None
            and bool(self.final_detector_states)
            and all(evaluation.status.value == "clear" for evaluation in self.final_detector_states)
        )


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
    proposal_processed: bool = False
    proposal_commit: str | None = None
    outcome_commit: str | None = None
    reflection_started: bool = False
    # How the first reflection attempt ran ("resume" or "fresh"); fixed when the
    # first LLM attempt starts, so a restarted broker keeps it. ``None`` when no
    # LLM reflection ran, and in ledgers written before the mode existed.
    reflection_session_mode: ReflectionSessionMode | None = None
    reflection_attempts: int = 0
    # Attempts after a validation rejection run in a fresh session rather than
    # resuming the responder session; defaulted for ledgers written earlier.
    reflection_fresh_retry_attempts: int = 0
    reflection_backend_completed: bool = False
    reflection_summary: str | None = None
    reflection_learning_decision: Literal["updated", "no_change"] | None = None
    reflection_no_change_reason: str | None = None
    reflection_proposed_changes: list[str] = Field(default_factory=list)
    # Provider accounting summed over every reflection attempt for the incident.
    reflection_usage: dict[str, int | float] = Field(default_factory=dict)
    reflection_validation_error: str | None = None
    # The last reflection backend failure (a crashed or failed model turn).
    # Failed turns count as attempts, so a backend that always fails is bounded.
    reflection_backend_error: str | None = None
    # Set when the broker recorded a deterministic no-op reflection instead of
    # running an LLM turn (a repeated exact-match success).
    reflection_skipped_reason: str | None = None
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
        reflector: OutcomeReflector | None = None,
        repair_policy: Literal["commit", "recorded-actions"] = "commit",
        max_reflection_attempts: int = 3,
        reflection_session: ReflectionSessionMode = "resume",
    ) -> None:
        if repair_policy not in ("commit", "recorded-actions"):
            raise ValueError(f"unsupported repair policy: {repair_policy!r}")
        if max_reflection_attempts < 1:
            raise ValueError("max_reflection_attempts must be at least 1")
        if reflection_session not in REFLECTION_SESSION_MODES:
            raise ValueError(f"unsupported reflection session mode: {reflection_session!r}")
        self.target_repository = target_repository.resolve()
        self.worktrees = WorktreeManager(self.target_repository, worktree_root)
        self.broker = broker or CommitBroker(self.target_repository)
        self.responder_backend = responder_backend
        self.responder_model = responder_model
        self.checkpoint = checkpoint or (lambda _stage: None)
        self.reflector = reflector
        self.repair_policy = repair_policy
        self.max_reflection_attempts = max_reflection_attempts
        self.reflection_session: ReflectionSessionMode = reflection_session
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
            if closure.request.repair_policy != self.repair_policy:
                raise BrokerServiceError(
                    "closure repair policy does not match the broker repair policy: "
                    f"{closure.request.repair_policy!r} != {self.repair_policy!r}"
                )
            if ledger.closure is None:
                ledger.closure = closure
                ledger.responder_session_id = None if closure.result is None else closure.result.responder_session_id
                self._save(ledger)
            else:
                closure = ledger.closure
            ledger = self._recover_commits(ledger)
            worktree = Path(ledger.worktree)

            if not ledger.proposal_processed and ledger.outcome_commit is None:
                changed_paths = self.broker.changed_paths(worktree)
                committed_changes = self.broker.has_committed_changes(worktree)
                if changed_paths or committed_changes or self.repair_policy == "commit":
                    proposal = self.broker.commit_proposal(
                        incident_worktree=worktree,
                        incident_id=incident_id,
                        allow_empty=not changed_paths and not committed_changes,
                    )
                    self.checkpoint("proposal_commit_unrecorded")
                    ledger.proposal_commit = proposal.commit_sha
                else:
                    self._validate_recorded_actions(closure)
                ledger.proposal_processed = True
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
        health_verified = closure.health_verified
        surfaced = [playbook.path for playbook in closure.request.surfaced_playbooks]
        applied = [] if result is None else [playbook.path for playbook in result.applied_playbooks]
        return derive_outcome(
            OutcomeFacts(
                request=closure.request,
                result=result,
                dispatch_error=closure.dispatch_error,
                final_health_detector_state=closure.final_detector_states,
                incident_detector_states=closure.incident_detector_states,
                final_state_changes=closure.final_state_changes,
                health_cleared_at=closure.health_cleared_at,
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

    @staticmethod
    def _validate_recorded_actions(closure: BrokerClosure) -> None:
        result = closure.result
        health_verified = closure.health_verified
        if not health_verified or result is None or result.status.value != "completed":
            return
        if not any(action.success for action in result.repair_actions):
            raise BrokerServiceError(
                "a confirmed repair without source changes requires at least one successful recorded repair action"
            )

    def _recover_commits(self, ledger: BrokerLedger) -> BrokerLedger:
        changed = False
        if ledger.proposal_commit is None:
            proposal = self.broker.find_attributed_commit(ledger.incident_id, "proposal")
            if proposal is not None:
                ledger.proposal_commit = proposal
                ledger.proposal_processed = True
                changed = True
        elif not ledger.proposal_processed:
            ledger.proposal_processed = True
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
                if not changed_paths:
                    # A completed no-op reflection is still an attributable,
                    # durable event. Its tree is identical to the already
                    # validated outcome tree, so retain those canaries.
                    ledger.validator_evidence_commit = reflection
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
        health_verified = closure.health_verified
        # The broker owns learning: a recovery the responder is not credited
        # with is never learned, whatever the reflector would decide (F4, F8).
        if outcome.classification in _UNCREDITED_RECOVERIES or not self.reflector.should_reflect(
            outcome, health_verified=health_verified, session_id=session_id
        ):
            ledger.reflection_completed = True
            self._save(ledger)
            return ledger
        changed_paths = self.broker.proposal_changed_paths(worktree)
        if not ledger.reflection_started and ledger.reflection_attempts == 0 and not changed_paths:
            skipped_reason = self._exact_match_noop_reason(
                closure, outcome, repository, health_verified=health_verified
            )
            if skipped_reason is not None:
                ledger.reflection_skipped_reason = skipped_reason
                ledger.reflection_backend_completed = True
                ledger.reflection_summary = "Deterministic no-op reflection: " + skipped_reason
                ledger.reflection_learning_decision = "no_change"
                ledger.reflection_no_change_reason = skipped_reason
                ledger.reflection_proposed_changes = []
                self._save(ledger)
                return self._commit_noop_reflection(ledger, worktree)
        retry_feedback = ledger.reflection_validation_error
        if ledger.reflection_started and not ledger.reflection_backend_completed:
            if changed_paths:
                self._rollback_incomplete_reflection(worktree)
                changed_paths = []
            ledger.reflection_started = False
            self._save(ledger)
        if ledger.reflection_attempts >= self.max_reflection_attempts:
            if changed_paths:
                self._rollback_incomplete_reflection(worktree)
            ledger.reflection_backend_completed = True
            ledger.reflection_summary = "No operational-memory update was accepted after bounded validation."
            ledger.reflection_learning_decision = "no_change"
            if ledger.reflection_validation_error is None and ledger.reflection_backend_error is not None:
                ledger.reflection_no_change_reason = (
                    "Learning was attempted, but the reflection backend failed on every attempt: "
                    + ledger.reflection_backend_error
                )
            else:
                ledger.reflection_no_change_reason = (
                    "Learning was attempted, but every proposed update failed independent validation: "
                    + (ledger.reflection_validation_error or "unknown validation failure")
                )
            ledger.reflection_proposed_changes = []
            self._save(ledger)
            return self._commit_noop_reflection(ledger, worktree)
        if not ledger.reflection_backend_completed:
            if ledger.reflection_session_mode is None:
                ledger.reflection_session_mode = self.reflection_session
            ledger.reflection_started = True
            # The attempt is counted here, before the backend call, and saved
            # durably: a broker killed mid-turn (SIGKILL, say) never reaches
            # the exception handler below, or any other code in this
            # process, so counting the attempt only after the call returns
            # or raises would let repeated kills retry the same incident
            # forever. Counting it before the call bounds that regardless of
            # when the process dies.
            ledger.reflection_attempts += 1
            self._save(ledger)
            try:
                turn = self.reflector.resume(
                    session_id=session_id or "",
                    incident_id=ledger.incident_id,
                    worktree=worktree,
                    outcome=outcome,
                    history=outcomes,
                    outcome_commit=ledger.outcome_commit or "",
                    validation_feedback=retry_feedback,
                    topology_review=self._topology_review(ledger),
                    rejected_proposal_diff=self._rejected_reflection_diff(ledger) if retry_feedback else None,
                    session_mode=ledger.reflection_session_mode,
                    closure=closure,
                )
            except Exception as exc:
                if self.broker.proposal_changed_paths(worktree):
                    self._rollback_incomplete_reflection(worktree)
                ledger.reflection_backend_error = f"{type(exc).__name__}: {exc}"[:2000]
                ledger.reflection_started = False
                self._save(ledger)
                raise
            if retry_feedback:
                ledger.reflection_fresh_retry_attempts += 1
            for key, value in turn.usage.items():
                ledger.reflection_usage[key] = ledger.reflection_usage.get(key, 0) + value
            ledger.reflection_backend_completed = True
            ledger.reflection_summary = turn.summary
            ledger.reflection_learning_decision = turn.learning_decision
            ledger.reflection_no_change_reason = turn.no_change_reason
            ledger.reflection_proposed_changes = list(turn.proposed_changes)
            self._save(ledger)
            self.checkpoint("reflection_resumed_unrecorded")
            changed_paths = self.broker.proposal_changed_paths(worktree)
        if changed_paths:
            if ledger.reflection_learning_decision != "updated":
                error = "reflection changed files but did not declare learning_decision=updated"
                self._reject_reflection(ledger, worktree, error)
                raise BrokerServiceError(error)
            detector_paths = sorted(
                path
                for path in changed_paths
                if path.startswith(".sdo/diagnostics/detectors/incidents/") or path == ".sdo/diagnostics/manifest.yaml"
            )
            if (
                outcome.classification == OutcomeClassification.SUCCESS
                and outcome.confirmed_root_causes
                and not detector_paths
                and not self._learned_incident_detector_fired(closure)
            ):
                error = (
                    "successful confirmed incident reflection must include a sharp fault-specific detector update "
                    "under .sdo/diagnostics/detectors/incidents/ and register it in the detector manifest"
                )
                self._reject_reflection(ledger, worktree, error)
                raise BrokerServiceError(error)
            try:
                reflection = self.broker.commit_proposal(
                    incident_worktree=worktree,
                    incident_id=ledger.incident_id,
                    phase="reflection",
                )
            except (CommitBrokerError, MemoryValidationError) as exc:
                self._reject_reflection(ledger, worktree, str(exc))
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
        else:
            if ledger.reflection_learning_decision != "no_change" or not ledger.reflection_no_change_reason:
                error = "reflection made no changes without declaring learning_decision=no_change and a concrete reason"
                self._reject_reflection(ledger, worktree, error)
                raise BrokerServiceError(error)
            return self._commit_noop_reflection(ledger, worktree, clear_error=True)
        ledger.reflection_completed = True
        self._save(ledger)
        return ledger

    def _learned_incident_detector_fired(self, closure: BrokerClosure) -> bool:
        """Whether an already-registered responder-owned incident detector raised a finding.

        Such an incident already has its sharp detector, so a reflection may
        refine only the playbook instead of making a cosmetic detector edit.
        """

        try:
            manifest = MemoryRepository(self.target_repository).diagnostics()
        except (MemoryRepositoryError, ValueError):
            return False
        incident_detectors = {
            detector.id
            for detector in manifest.detectors
            if detector.detector_class == "incident" and detector.owner == ArtifactOwner.RESPONDER
        }
        return any(finding.detector_id in incident_detectors for finding in closure.request.findings)

    @staticmethod
    def _exact_match_noop_reason(
        closure: BrokerClosure,
        outcome: OutcomeRecord,
        repository: MemoryRepository,
        *,
        health_verified: bool,
    ) -> str | None:
        """Why reflection can be skipped for a repeated exact-match success, or ``None``.

        Only a fully successful, verified repair of an incident whose validated
        incident detector and playbook already encode it qualifies: the warm
        rule held (an exact-fingerprint prior outcome surfaced a playbook owned
        by a registered incident detector), the responder applied that
        playbook, every repair action and verification passed, and the
        controller verified health. The owning incident detector must not be
        firing in its latest post-response evaluation; a detector with no
        post-response evaluation (it never fired, so the controller did not
        track it) is accepted because health verification already cleared.
        Anything else runs the full reflection turn.
        """

        result = closure.result
        if (
            not health_verified
            or outcome.classification != OutcomeClassification.SUCCESS
            or result is None
            or result.status.value != "completed"
            or not result.confirmed_root_causes
            or any(not action.success for action in result.repair_actions)
            or any(not evidence.passed for evidence in result.verification_evidence)
        ):
            return None
        try:
            manifest = repository.diagnostics()
        except (MemoryRepositoryError, ValueError):
            return None
        applied = {playbook.path for playbook in result.applied_playbooks}
        matches = [match for match in warm_playbook_matches(closure.request, manifest) if match.path in applied]
        if not matches:
            return None
        latest = {state.detector_id: state for state in closure.incident_detector_states}
        detector_states: list[str] = []
        for detector_id in sorted({match.detector.id for match in matches}):
            state = latest.get(detector_id)
            if state is None:
                detector_states.append(f"{detector_id} not evaluated after the response")
            elif state.status == DetectorEvaluationStatus.CLEAR and not state.fingerprints:
                detector_states.append(f"{detector_id} clear after the response")
            else:
                return None
        playbooks = ", ".join(
            f"{match.path} (detector {match.detector.id} "
            f"{'fired' if match.detector_fired else 'had not fired'} at dispatch; surfaced via "
            f"{'+'.join(match.sources)})"
            for match in matches
        )
        priors = sorted({incident for match in matches for incident in match.prior_incidents})
        return (
            f"repeated exact-match success: prior verified outcome(s) {', '.join(priors)} matched by exact "
            f"fingerprint; the responder applied validated incident playbook(s) {playbooks}; every repair action and "
            "verification passed; the controller verified health; incident detector state: "
            f"{'; '.join(detector_states)}; existing memory already encodes this incident"
        )

    def _reject_reflection(self, ledger: BrokerLedger, worktree: Path, error: str) -> None:
        """Persist a rejection so the next attempt can retry from the rejected diff.

        The diff is written before the ledger so a retry never sees the error
        without the proposal it describes; the next attempt rolls the worktree
        back and starts a fresh session from this diff.
        """

        diff_path = self._rejected_reflection_diff_path(ledger.incident_id)
        try:
            diff = self._proposal_diff(worktree)
        except CommitBrokerError as exc:
            # The diff only shortens the retry; never let it mask the rejection.
            diff = f"(rejected proposal diff unavailable: {exc})\n"
        temporary = diff_path.with_suffix(".tmp")
        temporary.write_text(diff, encoding="utf-8")
        os.replace(temporary, diff_path)
        ledger.reflection_backend_completed = False
        ledger.reflection_validation_error = error
        self._save(ledger)

    def _rejected_reflection_diff(self, ledger: BrokerLedger) -> str | None:
        path = self._rejected_reflection_diff_path(ledger.incident_id)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def _rejected_reflection_diff_path(self, incident_id: str) -> Path:
        return self._ledger_path(incident_id).with_suffix(".reflection-rejected.diff")

    def _proposal_diff(self, worktree: Path) -> str:
        """Diff every committed, staged, dirty and untracked change against the target head."""

        changed_paths = self.broker.proposal_changed_paths(worktree)
        if not changed_paths:
            return ""
        target_head = self.broker._git(self.target_repository, "rev-parse", "HEAD").strip()
        index = self.state_root / f"diff-index-{os.getpid()}"
        env = {**os.environ, "GIT_INDEX_FILE": str(index), "GIT_LITERAL_PATHSPECS": "1"}
        try:
            self.broker._git(worktree, "read-tree", target_head, env=env)
            self.broker._git(worktree, "add", "--all", "--", *changed_paths, env=env)
            return self.broker._git(worktree, "diff", "--cached", "--no-color", target_head, "--", env=env)
        finally:
            index.unlink(missing_ok=True)

    @staticmethod
    def _topology_review(ledger: BrokerLedger) -> TopologyReview | None:
        if not ledger.architecture_topology_fingerprint or not ledger.source_topology_fingerprint:
            return None
        return TopologyReview(
            architecture_topology_fingerprint=ledger.architecture_topology_fingerprint,
            source_topology_fingerprint=ledger.source_topology_fingerprint,
            stale_memory_detected=ledger.stale_memory_detected,
        )

    def _commit_noop_reflection(
        self,
        ledger: BrokerLedger,
        worktree: Path,
        *,
        clear_error: bool = False,
    ) -> BrokerLedger:
        reflection = self.broker.commit_proposal(
            incident_worktree=worktree,
            incident_id=ledger.incident_id,
            phase="reflection",
            allow_empty=True,
        )
        self.checkpoint("reflection_commit_unrecorded")
        ledger.reflection_commit = reflection.commit_sha
        # A no-op reflection preserves the validated outcome tree, so its
        # isolation evidence remains applicable.
        ledger.validator_evidence_commit = reflection.commit_sha
        ledger.accepted_detector_paths = []
        ledger.controller_update_required = False
        if clear_error:
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
        try:
            value = self.broker._git(self.target_repository, "rev-parse", f"{commit}:.sdo/diagnostics").strip()
        except CommitBrokerError as exc:
            raise BrokerServiceError(f"commit {commit} has no diagnostics tree") from exc
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
        target_head = self.broker._git(self.target_repository, "rev-parse", "HEAD").strip()
        self.broker._git(worktree, "reset", "--hard", target_head)
        self.broker._git(
            worktree,
            "clean",
            "-fd",
            "--",
            ".sdo/playbooks",
            ".sdo/diagnostics",
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
