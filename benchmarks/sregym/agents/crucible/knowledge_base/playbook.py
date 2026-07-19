"""Playbook data layer: parser, validator, store, and slug helper.

Playbooks are per-root-cause-class markdown documents containing concrete
triage and verification procedures that the Crucible diagnosis agent reads
at runtime. This module provides the diagnosis-flavored data shapes and
storage on top of the shared primitives in ``_playbook_common.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

from ._playbook_common import (
    ALLOWED_CREATED_FROM,
    META_CLOSE,
    META_OPEN,
    REQUIRED_META_KEYS,
    VAGUE_PHRASES,
    PlaybookStoreBase,
    extract_meta_block,
    extract_sections,
    parse_bullet_list,
    parse_numbered_steps,
    slugify,
    validate_meta_block,
    validate_vague_phrases,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Summary",
    "Symptoms",
    "Triage Procedure",
    "Verification Procedure",
    "Required Evidence",
    "Known Distractors",
    "Failure Patterns",
)


# Re-exports for backward compatibility with callers that imported these from
# this module before the common helpers were extracted.
__all__ = [
    "ALLOWED_CREATED_FROM",
    "META_CLOSE",
    "META_OPEN",
    "REQUIRED_META_KEYS",
    "REQUIRED_SECTIONS",
    "VAGUE_PHRASES",
    "Playbook",
    "PlaybookStore",
    "PlaybookValidationError",
    "slugify",
    "validate_playbook",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PlaybookValidationError(ValueError):
    """Raised when a playbook fails structural validation.

    The ``violations`` attribute carries every detected problem so that an
    LLM-based synthesizer can pass them back as feedback for fix-and-retry.
    """

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        message = "Playbook validation failed: " + "; ".join(self.violations)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_playbook(text: str) -> list[str]:
    """Return a list of human-readable violations. Empty list means valid."""
    meta = extract_meta_block(text)
    violations = list(validate_meta_block(meta))

    sections = extract_sections(text)
    violations.extend(
        f"section: missing required '## {required}'" for required in REQUIRED_SECTIONS if required not in sections
    )

    if "Symptoms" in sections and not parse_bullet_list(sections["Symptoms"]):
        violations.append("Symptoms: must contain at least one '- ' bullet")

    if "Triage Procedure" in sections and not parse_numbered_steps(sections["Triage Procedure"]):
        violations.append("Triage Procedure: must contain at least one numbered step")

    if "Verification Procedure" in sections and not parse_numbered_steps(sections["Verification Procedure"]):
        violations.append("Verification Procedure: must contain at least one numbered step")

    if "Required Evidence" in sections:
        body = sections["Required Evidence"]
        items = parse_bullet_list(body)
        if not items:
            violations.append("Required Evidence: must contain at least one item")
        has_checkbox = any(line.lstrip().startswith("- [ ]") for line in body.splitlines())
        if not has_checkbox:
            violations.append("Required Evidence: must contain at least one '- [ ]' checkbox item")

    violations.extend(validate_vague_phrases(text))
    return violations


# ---------------------------------------------------------------------------
# Playbook model
# ---------------------------------------------------------------------------


class Playbook(BaseModel):
    """Structured representation of a playbook markdown document."""

    slug: str
    class_name: str
    seen: int = 0
    created_from: Literal["success", "recovery"]
    last_updated: str
    summary: str
    symptoms: list[str] = Field(default_factory=list)
    triage_procedure: list[str] = Field(default_factory=list)
    verification_procedure: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    known_distractors: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    markdown: str = ""

    @classmethod
    def parse(cls, text: str) -> Playbook:
        """Parse a markdown document into a Playbook.

        Raises ``PlaybookValidationError`` if the document is malformed.
        """
        violations = validate_playbook(text)
        if violations:
            raise PlaybookValidationError(violations)

        meta = extract_meta_block(text)
        assert meta is not None  # validated above

        sections = extract_sections(text)

        created_from_value = meta["created_from"]
        if created_from_value not in ALLOWED_CREATED_FROM:
            # Defensive: validator should have caught this.
            raise PlaybookValidationError([f"meta.created_from: invalid value '{created_from_value}'"])

        return cls(
            slug=meta["slug"],
            class_name=meta["class_name"],
            seen=int(meta["seen"]),
            created_from=cast("Literal['success', 'recovery']", created_from_value),
            last_updated=meta["last_updated"],
            summary=sections.get("Summary", "").strip(),
            symptoms=parse_bullet_list(sections.get("Symptoms", "")),
            triage_procedure=parse_numbered_steps(sections.get("Triage Procedure", "")),
            verification_procedure=parse_numbered_steps(sections.get("Verification Procedure", "")),
            required_evidence=parse_bullet_list(sections.get("Required Evidence", "")),
            known_distractors=parse_bullet_list(sections.get("Known Distractors", "")),
            failure_patterns=parse_bullet_list(sections.get("Failure Patterns", "")),
            markdown=text,
        )

    def to_markdown(self) -> str:
        """Return a valid markdown string for this playbook.

        If ``self.markdown`` is non-empty AND parses cleanly, returns it
        verbatim (round-trip preservation). Otherwise serializes from the
        structured fields.
        """
        if self.markdown and not validate_playbook(self.markdown):
            return self.markdown

        return self._serialize()

    def _serialize(self) -> str:
        lines: list[str] = []
        lines.append(f"# Playbook: {self.class_name}")
        lines.append("")
        lines.append(META_OPEN)
        lines.append(f"slug: {self.slug}")
        lines.append(f"class_name: {self.class_name}")
        lines.append(f"seen: {self.seen}")
        lines.append(f"created_from: {self.created_from}")
        lines.append(f"last_updated: {self.last_updated}")
        lines.append(META_CLOSE)
        lines.append("")
        lines.append("## Summary")
        lines.append(self.summary)
        lines.append("")
        lines.append("## Symptoms")
        lines.extend(f"- {item}" for item in self.symptoms)
        lines.append("")
        lines.append("## Triage Procedure")
        lines.extend(self.triage_procedure)
        lines.append("")
        lines.append("## Verification Procedure")
        lines.extend(self.verification_procedure)
        lines.append("")
        lines.append("## Required Evidence (Submission Gate)")
        lines.extend(f"- [ ] {item}" for item in self.required_evidence)
        lines.append("")
        lines.append("## Known Distractors")
        lines.extend(f"- {item}" for item in self.known_distractors)
        lines.append("")
        lines.append("## Failure Patterns")
        lines.extend(f"- {item}" for item in self.failure_patterns)
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Playbook store
# ---------------------------------------------------------------------------


class PlaybookStore(PlaybookStoreBase):
    """Filesystem-backed store for diagnosis playbook markdown files."""

    def _parse(self, text: str) -> Playbook:
        return Playbook.parse(text)

    def _to_markdown(self, model: Playbook) -> str:
        return model.to_markdown()

    def load(self, slug: str) -> Playbook | None:  # type: ignore[override]
        return cast("Playbook | None", super().load(slug))

    def save(self, playbook: Playbook) -> Path:  # type: ignore[override]
        return super().save(playbook)
