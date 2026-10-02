"""Opt-in store of completed cold lifecycles, restored into fresh workspaces of the same source.

A cold lifecycle takes about eleven minutes of model time. Iteration runs and
retries start from a fresh copy of the same application source and re-author
the same ``.sdo`` handoff. This cache keeps the ``.sdo`` tree a cold lifecycle
produced, keyed by everything that can change that lifecycle, and restores it
into a clean workspace as one commit.

The cache is a source of candidates, not of trust. A restored lifecycle still
goes through ``reuse_initial_lifecycle_if_valid``, which checks the provenance,
the deployer assessment against the current source, the health objective, the
active topology and the independent validator verdict. A restore that fails
that check is reverted and the cold lifecycle runs. A run measuring a cold
lifecycle leaves the cache disabled.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

CACHE_DIR_ENV = "SDO_LIFECYCLE_SEED_CACHE_DIR"
_ENTRY_SCHEMA = "sdo.lifecycle-seed-cache/v1"
_SDO = ".sdo"

SeedEvent = Literal["hit", "miss", "stored", "restore-rejected", "skipped-existing-lifecycle"]


@dataclass(frozen=True)
class SeedInputs:
    """Everything besides the source commit that determines a cold lifecycle's output."""

    health_objective: str
    active_resources: Sequence[tuple[str, str]] | None
    validator_identity: str
    provider: str
    model: str
    #: Digest of the lifecycle authoring code, so a prompt or validator change never reuses an old handoff.
    code_digest: str

    def __post_init__(self) -> None:
        for name in ("health_objective", "validator_identity", "provider", "model", "code_digest"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")

    def canonical(self) -> dict[str, object]:
        resources = (
            None if self.active_resources is None else sorted([kind, name] for kind, name in self.active_resources)
        )
        return {
            "health_objective": self.health_objective,
            "active_resources": resources,
            "validator_identity": self.validator_identity,
            "provider": self.provider,
            "model": self.model,
            "code_digest": self.code_digest,
        }


def lifecycle_code_digest() -> str:
    """Digest of the lifecycle authoring package and the sandbox that validates it."""

    package = Path(__file__).resolve().parent
    sandbox = package.parent.parent / "operational_memory" / "sandbox.py"
    digest = hashlib.sha256()
    for path in (*sorted(package.glob("*.py")), sandbox):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


class LifecycleSeedError(RuntimeError):
    """A seed-cache operation could not read or write the workspace."""


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise LifecycleSeedError(f"git {' '.join(args)} failed in {root}: {details}")
    return completed.stdout.strip()


@dataclass
class RestoredSeed:
    """A restored lifecycle that can still be undone before it is trusted."""

    repository: Path
    previous_head: str
    _cache: LifecycleSeedCache

    def revert(self) -> None:
        _git(self.repository, "reset", "--quiet", "--hard", self.previous_head)
        shutil.rmtree(self.repository / _SDO, ignore_errors=True)
        _git(self.repository, "clean", "--quiet", "-fd", "--", _SDO)
        self._cache.last_event = "restore-rejected"


class LifecycleSeedCache:
    """Cold lifecycle handoffs keyed by source commit and :class:`SeedInputs`.

    ``last_event`` reports what the most recent operation did, so a run can
    say whether its lifecycle was restored or authored.
    """

    def __init__(self, directory: Path) -> None:
        if not isinstance(directory, Path):
            raise TypeError("directory must be a pathlib.Path")
        if not directory.is_absolute():
            raise ValueError(f"lifecycle seed cache directory must be absolute: {directory}")
        self.directory = directory
        self.last_event: SeedEvent | None = None

    @classmethod
    def from_env(cls) -> LifecycleSeedCache | None:
        raw = os.getenv(CACHE_DIR_ENV, "").strip()
        return cls(Path(raw)) if raw else None

    @staticmethod
    def eligible(repository: Path) -> bool:
        """Only a workspace with no ``.sdo`` yet is a cold lifecycle's starting point."""

        return not (repository / _SDO).exists()

    def key(self, repository: Path, inputs: SeedInputs) -> str:
        source_commit = _git(repository, "rev-parse", "HEAD")
        payload = json.dumps(
            {"schema": _ENTRY_SCHEMA, "source_commit": source_commit, **inputs.canonical()},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _entry(self, key: str) -> Path:
        return self.directory / key

    def restore(self, repository: Path, key: str) -> RestoredSeed | None:
        """Copy a stored ``.sdo`` into a clean workspace and commit it, or return ``None``."""

        if not self.eligible(repository):
            self.last_event = "skipped-existing-lifecycle"
            return None
        stored = self._entry(key) / "sdo"
        if not (self._entry(key) / "entry.json").is_file() or not stored.is_dir():
            self.last_event = "miss"
            return None
        previous_head = _git(repository, "rev-parse", "HEAD")
        shutil.copytree(stored, repository / _SDO, symlinks=True)
        _git(repository, "add", "--all", "--", _SDO)
        _git(
            repository,
            "-c",
            "user.name=SDO Lifecycle",
            "-c",
            "user.email=sdo-lifecycle@localhost",
            "commit",
            "--quiet",
            "-m",
            "sdo: restore cold lifecycle from the seed cache",
        )
        self.last_event = "hit"
        return RestoredSeed(repository=repository, previous_head=previous_head, _cache=self)

    def store(self, repository: Path, key: str) -> None:
        """Keep the workspace's ``.sdo`` under ``key``; the first writer for a key wins."""

        source = repository / _SDO
        if not source.is_dir():
            raise ValueError(f"cannot store a lifecycle: no {_SDO} in {repository}")
        final = self._entry(key)
        if final.exists():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.directory))
        try:
            shutil.copytree(source, temporary / "sdo", symlinks=True)
            (temporary / "entry.json").write_text(
                json.dumps(
                    {"schema_version": _ENTRY_SCHEMA, "key": key, "stored_at": datetime.now(timezone.utc).isoformat()},
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            try:
                os.rename(temporary, final)
            except OSError:
                if not final.exists():
                    raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        self.last_event = "stored"
