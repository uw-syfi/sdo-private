from __future__ import annotations

import fcntl
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sdo.operational_memory.validation import MemoryValidationError, MemoryValidator

if TYPE_CHECKING:
    from sdo.operational_memory.models import ArtifactOwner, ValidatorNetworkPolicyCanary


BROKER_AUTHOR_EMAIL = "sdo-commit-broker@localhost"
VALIDATION_PASSED_TRAILER = "SDO-Validation: passed"


class CommitBrokerError(RuntimeError):
    """Raised when the transactional memory commit cannot complete."""


@dataclass(frozen=True)
class CommitResult:
    commit_sha: str
    changed_paths: tuple[str, ...]
    validator_network_policy_canaries: tuple[ValidatorNetworkPolicyCanary, ...] = ()


class ProposalValidator(Protocol):
    def validate(self, worktree: Path, changed_paths: list[str]) -> None: ...


class RejectingProposalValidator:
    def validate(self, worktree: Path, changed_paths: list[str]) -> None:
        del worktree
        if changed_paths:
            raise MemoryValidationError("source repair validation is not configured")


class CommandProposalValidator:
    def __init__(self, commands: list[list[str]], *, timeout_seconds: int = 600) -> None:
        if not commands:
            raise ValueError("at least one proposal validation command is required")
        self.commands = commands
        self.timeout_seconds = timeout_seconds

    def validate(self, worktree: Path, changed_paths: list[str]) -> None:
        if not changed_paths:
            return
        for command in self.commands:
            completed = subprocess.run(
                command,
                cwd=worktree,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                details = completed.stderr.strip() or completed.stdout.strip()
                raise MemoryValidationError(f"proposal validation {' '.join(command)} failed: {details}")


class CommitBroker:
    def __init__(
        self,
        target_repository: Path,
        *,
        validator: MemoryValidator | None = None,
        proposal_validator: ProposalValidator | None = None,
    ) -> None:
        self.target_repository = target_repository.resolve()
        self.validator = validator or MemoryValidator()
        self.proposal_validator = proposal_validator or RejectingProposalValidator()

    def commit(
        self,
        *,
        incident_worktree: Path,
        incident_id: str,
        actor: ArtifactOwner,
        phase: str = "memory",
    ) -> CommitResult:
        if not incident_id.strip():
            raise CommitBrokerError("incident_id is required")
        worktree = incident_worktree.resolve()
        if worktree == self.target_repository:
            raise CommitBrokerError("incident proposal must use an isolated Git worktree")
        common_dir = self._git_common_dir()
        common_dir.mkdir(parents=True, exist_ok=True)
        lock_path = common_dir / "sdo-commit-broker.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._commit_locked(worktree=worktree, incident_id=incident_id, actor=actor, phase=phase)

    def commit_proposal(
        self,
        *,
        incident_worktree: Path,
        incident_id: str,
        phase: str = "proposal",
        allow_empty: bool = False,
    ) -> CommitResult:
        worktree = incident_worktree.resolve()
        if worktree == self.target_repository:
            raise CommitBrokerError("incident proposal must use an isolated Git worktree")
        lock_path = self._git_common_dir() / "sdo-commit-broker.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self._git(self.target_repository, "status", "--porcelain").strip():
                raise CommitBrokerError("target repository must be clean")
            changed_paths = self._changed_paths(worktree)
            if changed_paths:
                self._ensure_safe_proposal_paths(changed_paths)
            self._rebase_onto_target(worktree, changed_paths)
            changed_paths = self._changed_paths(worktree)
            if changed_paths or not allow_empty:
                self._ensure_safe_proposal_paths(changed_paths)
            memory_paths = [path for path in changed_paths if path.startswith(".sdo/")]
            repair_paths = [path for path in changed_paths if not path.startswith(".sdo/")]
            canary_evidence: tuple[ValidatorNetworkPolicyCanary, ...] = ()
            if phase == "proposal" and memory_paths:
                raise MemoryValidationError(
                    "mitigation proposals may not edit .sdo memory before independent health verification"
                )
            if memory_paths:
                from sdo.operational_memory.models import ArtifactOwner

                canary_evidence = self.validator.validate(
                    worktree,
                    actor=ArtifactOwner.RESPONDER,
                    changed_paths=memory_paths,
                    baseline_root=self.target_repository,
                )
            self.proposal_validator.validate(worktree, repair_paths)
            return self._commit_validated(
                worktree=worktree,
                incident_id=incident_id,
                actor="responder",
                phase=phase,
                changed_paths=changed_paths,
                stage_root=".",
                allow_empty=allow_empty and not changed_paths,
                validator_network_policy_canaries=canary_evidence,
            )

    def _commit_locked(
        self,
        *,
        worktree: Path,
        incident_id: str,
        actor: ArtifactOwner,
        phase: str,
    ) -> CommitResult:
        if self._git(self.target_repository, "status", "--porcelain").strip():
            raise CommitBrokerError("target repository must be clean")
        changed_paths = self._changed_paths(worktree)
        self._ensure_memory_only(changed_paths)
        self._rebase_onto_target(worktree, changed_paths)
        changed_paths = self._changed_paths(worktree)
        self._ensure_memory_only(changed_paths)
        canary_evidence = self.validator.validate(
            worktree,
            actor=actor,
            changed_paths=changed_paths,
            baseline_root=self.target_repository,
        )

        return self._commit_validated(
            worktree=worktree,
            incident_id=incident_id,
            actor=actor.value,
            phase=phase,
            changed_paths=changed_paths,
            stage_root=".sdo",
            validator_network_policy_canaries=canary_evidence,
        )

    def _commit_validated(
        self,
        *,
        worktree: Path,
        incident_id: str,
        actor: str,
        phase: str,
        changed_paths: list[str],
        stage_root: str,
        allow_empty: bool = False,
        validator_network_policy_canaries: tuple[ValidatorNetworkPolicyCanary, ...] = (),
    ) -> CommitResult:
        self._git(worktree, "add", "--all", "--", stage_root)
        message = (
            f"sdo({incident_id}): validated operational memory\n\n"
            f"SDO-Incident: {incident_id}\n"
            f"SDO-Actor: {actor}\n"
            f"SDO-Phase: {phase}\n"
            f"{VALIDATION_PASSED_TRAILER}"
        )
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "SDO Commit Broker",
            "GIT_AUTHOR_EMAIL": BROKER_AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": "SDO Commit Broker",
            "GIT_COMMITTER_EMAIL": BROKER_AUTHOR_EMAIL,
        }
        commit_args = ["commit"]
        if allow_empty:
            commit_args.append("--allow-empty")
        commit_args.extend(["-m", message])
        self._git(worktree, *commit_args, env=env)
        commit_sha = self._git(worktree, "rev-parse", "HEAD").strip()
        try:
            self._git(self.target_repository, "merge", "--ff-only", commit_sha)
        except CommitBrokerError as exc:
            raise CommitBrokerError("validated commit could not be fast-forwarded to target") from exc
        return CommitResult(
            commit_sha=commit_sha,
            changed_paths=tuple(changed_paths),
            validator_network_policy_canaries=validator_network_policy_canaries,
        )

    def validate_memory_state(
        self,
        worktree: Path,
        *,
        actor: ArtifactOwner,
        changed_paths: list[str],
    ) -> tuple[ValidatorNetworkPolicyCanary, ...]:
        """Revalidate an already-committed memory tree for crash recovery evidence."""
        return self.validator.validate(
            worktree,
            actor=actor,
            changed_paths=changed_paths,
        )

    def find_attributed_commit(self, incident_id: str, phase: str) -> str | None:
        output = self._git(self.target_repository, "log", "--format=%H%x1f%B%x1e")
        for record in output.split("\x1e"):
            if "\x1f" not in record:
                continue
            commit_sha, message = record.split("\x1f", maxsplit=1)
            if f"SDO-Incident: {incident_id}" in message and f"SDO-Phase: {phase}" in message:
                return commit_sha.strip()
        return None

    def changed_paths(self, incident_worktree: Path) -> list[str]:
        return self._changed_paths(incident_worktree.resolve())

    def has_committed_changes(self, incident_worktree: Path) -> bool:
        """Return whether the incident branch contains commits absent from the target."""
        worktree = incident_worktree.resolve()
        target_head = self._git(self.target_repository, "rev-parse", "HEAD").strip()
        count = self._git(worktree, "rev-list", "--count", f"{target_head}..HEAD").strip()
        return int(count) > 0

    def proposal_changed_paths(self, incident_worktree: Path) -> list[str]:
        """Return dirty and incident-committed paths absent from the target."""
        worktree = incident_worktree.resolve()
        paths = set(self._changed_paths(worktree))
        target_head = self._git(self.target_repository, "rev-parse", "HEAD").strip()
        if self.has_committed_changes(worktree):
            committed = self._git(worktree, "diff", "--name-only", "-z", f"{target_head}...HEAD")
            paths.update(path for path in committed.split("\0") if path)
        return sorted(paths)

    def _rebase_onto_target(self, worktree: Path, changed_paths: list[str]) -> None:
        del changed_paths  # Recompute the dirty subset; callers may also include committed divergence.
        target_head = self._git(self.target_repository, "rev-parse", "HEAD").strip()
        worktree_head = self._git(worktree, "rev-parse", "HEAD").strip()
        if target_head == worktree_head:
            return
        dirty_paths = self._changed_paths(worktree)
        stashed = bool(dirty_paths)
        if stashed:
            self._git(
                worktree,
                "stash",
                "push",
                "--include-untracked",
                "-m",
                "sdo-commit-broker-rebase",
                "--",
                *dirty_paths,
            )
        try:
            self._git(worktree, "rebase", target_head)
        except CommitBrokerError as exc:
            subprocess.run(
                ["git", "-C", str(worktree), "rebase", "--abort"],
                check=False,
                capture_output=True,
                text=True,
            )
            if stashed:
                subprocess.run(
                    ["git", "-C", str(worktree), "stash", "pop"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            raise CommitBrokerError("proposal conflicts with a newer accepted memory commit") from exc
        if stashed:
            try:
                self._git(worktree, "stash", "pop")
            except CommitBrokerError as exc:
                raise CommitBrokerError("proposal conflicts with a newer accepted memory commit") from exc
        # A responder may commit its repair before returning.  Preserve the
        # tree, but never fast-forward that untrusted commit directly into the
        # target: squash all rebased divergence back onto the trusted target
        # head so path checks, independent validation, and broker attribution
        # cover the complete proposal.
        if int(self._git(worktree, "rev-list", "--count", f"{target_head}..HEAD")) > 0:
            self._git(worktree, "reset", "--soft", target_head)

    def _git_common_dir(self) -> Path:
        raw = self._git(self.target_repository, "rev-parse", "--git-common-dir").strip()
        path = Path(raw)
        if not path.is_absolute():
            path = self.target_repository / path
        return path.resolve()

    @staticmethod
    def _ensure_memory_only(changed_paths: list[str]) -> None:
        if not changed_paths:
            raise MemoryValidationError("proposal has no changed operational-memory files")
        outside = [path for path in changed_paths if path != ".sdo" and not path.startswith(".sdo/")]
        if outside:
            raise MemoryValidationError(f"proposal changes file outside .sdo: {outside[0]}")

    @staticmethod
    def _ensure_safe_proposal_paths(changed_paths: list[str]) -> None:
        if not changed_paths:
            raise MemoryValidationError("proposal has no changed files")
        for path in changed_paths:
            parts = Path(path).parts
            if Path(path).is_absolute() or ".." in parts or not parts:
                raise MemoryValidationError(f"unsafe proposal path: {path}")
            lowered = {part.lower() for part in parts}
            if ".git" in lowered or ".env" in lowered or any("secret" in part for part in lowered):
                raise MemoryValidationError(f"proposal may not modify secret-bearing path: {path}")

    @staticmethod
    def _changed_paths(worktree: Path) -> list[str]:
        unstaged = (
            subprocess.run(
                ["git", "-C", str(worktree), "diff", "--name-only", "-z", "HEAD", "--"],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .split("\0")
        )
        staged = (
            subprocess.run(
                ["git", "-C", str(worktree), "diff", "--cached", "--name-only", "-z", "HEAD", "--"],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .split("\0")
        )
        untracked = (
            subprocess.run(
                ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .split("\0")
        )
        return sorted({path for path in [*unstaged, *staged, *untracked] if path})

    @staticmethod
    def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip()
            raise CommitBrokerError(f"git {' '.join(args)} failed: {details}")
        return completed.stdout
