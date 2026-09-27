"""Opt-in store of independent lifecycle validation verdicts shared across workspaces.

Lifecycle reuse already skips the detector validator when the workspace's own
attestation names the same validator identity and diagnostics digest. Benchmark
pipelines, however, start every run from a fresh copy of a seed workspace and
discard the copy afterwards, so that attestation never outlives the run. This
cache keeps the same verdict, under the same key, outside the workspace.

The validator examines only ``.sdo/diagnostics`` inside an immutable image, so
the key (validator identity plus diagnostics digest) is exactly the trust basis
of the in-workspace attestation. Only passing validations by an identified
validator are stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

CACHE_DIR_ENV = "SDO_LIFECYCLE_VALIDATION_CACHE_DIR"
_ENTRY_SCHEMA = "sdo.lifecycle-validation-cache/v1"

ValidationSource = Literal["workspace-attestation", "validation-cache", "validator"]


class LifecycleValidationCache:
    """Validator verdicts keyed by validator identity and diagnostics digest.

    ``source`` reports how the most recent reuse check was satisfied, so a
    receipt can tell a cached lifecycle from a freshly validated one.
    """

    def __init__(self, directory: Path) -> None:
        if not isinstance(directory, Path):
            raise TypeError("directory must be a pathlib.Path")
        if not directory.is_absolute():
            raise ValueError(f"validation cache directory must be absolute: {directory}")
        self.directory = directory
        self.source: ValidationSource | None = None

    @classmethod
    def from_env(cls) -> LifecycleValidationCache | None:
        raw = os.getenv(CACHE_DIR_ENV, "").strip()
        return cls(Path(raw)) if raw else None

    def _entry_path(self, validator_identity: str, diagnostics_digest: str) -> Path:
        key = hashlib.sha256(f"{_ENTRY_SCHEMA}\0{validator_identity}\0{diagnostics_digest}".encode()).hexdigest()
        return self.directory / f"{key}.json"

    def contains(self, validator_identity: str, diagnostics_digest: str) -> bool:
        path = self._entry_path(validator_identity, diagnostics_digest)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(entry, dict)
            and entry.get("schema_version") == _ENTRY_SCHEMA
            and entry.get("validator_identity") == validator_identity
            and entry.get("diagnostics_digest") == diagnostics_digest
        )

    def report(self) -> dict[str, str | None]:
        return {"cache": "enabled", "source": self.source}

    def record(self, validator_identity: str, diagnostics_digest: str) -> None:
        """Store a passing validation atomically; concurrent writers agree on the content."""

        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": _ENTRY_SCHEMA,
            "validator_identity": validator_identity,
            "diagnostics_digest": diagnostics_digest,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._entry_path(validator_identity, diagnostics_digest)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.directory, prefix=f".{path.stem}-", delete=False
        ) as handle:
            json.dump(entry, handle, sort_keys=True)
            temporary = Path(handle.name)
        os.replace(temporary, path)


def validation_report(cache: LifecycleValidationCache | None) -> dict[str, str | None]:
    """Receipt field telling a cached lifecycle validation from a fresh one.

    ``source`` is ``None`` when no reuse check reached validation, for example
    when a fresh lifecycle ran or a persistent controller skipped revalidation.
    """

    return cache.report() if cache is not None else {"cache": "disabled", "source": None}
