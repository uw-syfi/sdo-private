from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when an isolated incident worktree cannot be managed safely."""


@dataclass(frozen=True)
class IncidentWorktree:
    incident_id: str
    path: Path
    base_commit: str


class WorktreeManager:
    def __init__(self, target_repository: Path, worktree_root: Path) -> None:
        self.target_repository = target_repository.resolve()
        self.worktree_root = worktree_root.resolve()
        if self.worktree_root == self.target_repository or self.target_repository in self.worktree_root.parents:
            raise WorktreeError("incident worktree root must be outside the target repository")

    def prepare(self, incident_id: str) -> IncidentWorktree:
        if not incident_id.strip():
            raise WorktreeError("incident_id is required")
        base_commit = self._git(self.target_repository, "rev-parse", "HEAD")
        path = self.path_for(incident_id)
        if path.exists():
            try:
                existing_head = self._git(path, "rev-parse", "HEAD")
            except WorktreeError as exc:
                raise WorktreeError(f"existing incident path is not a Git worktree: {path}") from exc
            return IncidentWorktree(incident_id=incident_id, path=path, base_commit=existing_head)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._git(self.target_repository, "worktree", "add", "--detach", str(path), base_commit)
        return IncidentWorktree(incident_id=incident_id, path=path, base_commit=base_commit)

    def cleanup(self, incident_id: str) -> None:
        path = self.path_for(incident_id)
        if not path.exists():
            self._git(self.target_repository, "worktree", "prune")
            return
        self._git(self.target_repository, "worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path)

    def path_for(self, incident_id: str) -> Path:
        digest = hashlib.sha256(incident_id.encode()).hexdigest()[:12]
        readable = "".join(character if character.isalnum() else "-" for character in incident_id.lower()).strip("-")
        readable = readable[:32] or "incident"
        path = (self.worktree_root / f"{readable}-{digest}").resolve()
        try:
            path.relative_to(self.worktree_root)
        except ValueError as exc:
            raise WorktreeError("derived worktree path escapes configured root") from exc
        return path

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip()
            raise WorktreeError(f"git {' '.join(args)} failed: {details}")
        return completed.stdout.strip()
