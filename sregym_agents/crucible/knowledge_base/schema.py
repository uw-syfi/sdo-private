"""KB schema versioning: filename mappings and migration between versions."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 2

_SCHEMA_FILE = "kb_schema.json"

# Prior file fields that differ between schema versions.
_PRIOR_FIELDS = ("diagnosis_priors", "triage_priors", "arbitration_priors")


@dataclasses.dataclass(frozen=True)
class KBSchema:
    """Filename mappings for a KB schema version."""

    version: int
    summary: str
    lessons: str
    architecture: str
    incidents_dir: str
    diagnosis_priors: str
    triage_priors: str
    arbitration_priors: str
    verification_priors: str = ""


SCHEMA_V1 = KBSchema(
    version=1,
    summary="long_term_summary.md",
    lessons="operational_lessons.md",
    architecture="architecture.md",
    incidents_dir="incidents",
    diagnosis_priors="diagnosis_heuristics.md",
    triage_priors="triage_heuristics.md",
    arbitration_priors="arbitration_heuristics.md",
)

SCHEMA_V2 = KBSchema(
    version=2,
    summary="long_term_summary.md",
    lessons="operational_lessons.md",
    architecture="architecture.md",
    incidents_dir="incidents",
    diagnosis_priors="diagnosis_priors.md",
    triage_priors="triage_priors.md",
    arbitration_priors="arbitration_priors.md",
    verification_priors="verification_priors.md",
)

SCHEMAS: dict[int, KBSchema] = {1: SCHEMA_V1, 2: SCHEMA_V2}


def read_schema_version(kb_dir: Path) -> int:
    """Read schema version from kb_schema.json, defaulting to 1 if absent."""
    schema_file = kb_dir / _SCHEMA_FILE
    if schema_file.exists():
        return json.loads(schema_file.read_text())["version"]
    return 1


def write_schema_version(kb_dir: Path, version: int = CURRENT_SCHEMA_VERSION) -> None:
    """Write the schema version marker file."""
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / _SCHEMA_FILE).write_text(json.dumps({"version": version}))


def get_schema(kb_dir: Path) -> KBSchema:
    """Return the KBSchema for the given KB directory."""
    return SCHEMAS[read_schema_version(kb_dir)]


def migrate_to_current(kb_dir: Path) -> None:
    """Migrate a KB directory from its current schema to CURRENT_SCHEMA_VERSION.

    Renames files in place.  Safe to call multiple times (no-op if already current).
    """
    current = read_schema_version(kb_dir)
    if current >= CURRENT_SCHEMA_VERSION:
        return

    old = SCHEMAS[current]
    new = SCHEMAS[CURRENT_SCHEMA_VERSION]

    # Rename root-level prior files
    for field in _PRIOR_FIELDS:
        old_path = kb_dir / getattr(old, field)
        new_path = kb_dir / getattr(new, field)
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)
            logger.info("Migrated %s -> %s", old_path.name, new_path.name)

    write_schema_version(kb_dir)
    logger.info("KB schema migrated from v%d to v%d", current, CURRENT_SCHEMA_VERSION)
