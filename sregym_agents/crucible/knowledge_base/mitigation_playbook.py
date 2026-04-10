"""Mitigation playbook data layer: parser, validator, store.

Mitigation playbooks are per-root-cause-class markdown documents containing
the concrete fix-and-verify procedure that the Crucible mitigation subagent
executes at runtime. Each playbook is keyed by the same slug as the matching
diagnosis playbook (when one exists), but the two stores are independent —
either may exist without the other.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

from ._playbook_common import (
    ALLOWED_CREATED_FROM,
    META_CLOSE,
    META_OPEN,
    PlaybookStoreBase,
    extract_meta_block,
    extract_sections,
    parse_bullet_list,
    parse_numbered_steps,
    validate_meta_block,
    validate_vague_phrases,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Summary",
    "Applicability",
    "Mitigation Procedure",
    "Post-Mitigation Verification",
    "Required Evidence",
    "Known Pitfalls",
    "Failure Patterns",
)


__all__ = [
    "REQUIRED_SECTIONS",
    "MitigationPlaybook",
    "MitigationPlaybookStore",
    "MitigationPlaybookValidationError",
    "validate_mitigation_playbook",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MitigationPlaybookValidationError(ValueError):
    """Raised when a mitigation playbook fails structural validation."""

    def __init__(self, violations: list[str]):
        self.violations = list(violations)
        message = "Mitigation playbook validation failed: " + "; ".join(self.violations)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_mitigation_playbook(text: str) -> list[str]:
    """Return a list of human-readable violations. Empty list means valid."""
    meta = extract_meta_block(text)
    violations = list(validate_meta_block(meta))

    sections = extract_sections(text)
    violations.extend(
        f"section: missing required '## {required}'" for required in REQUIRED_SECTIONS if required not in sections
    )

    if "Applicability" in sections and not parse_bullet_list(sections["Applicability"]):
        violations.append("Applicability: must contain at least one '- ' bullet")

    if "Mitigation Procedure" in sections and not parse_numbered_steps(sections["Mitigation Procedure"]):
        violations.append("Mitigation Procedure: must contain at least one numbered step")

    if "Post-Mitigation Verification" in sections and not parse_numbered_steps(
        sections["Post-Mitigation Verification"]
    ):
        violations.append("Post-Mitigation Verification: must contain at least one numbered step")

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
# Mitigation playbook model
# ---------------------------------------------------------------------------


class MitigationPlaybook(BaseModel):
    """Structured representation of a mitigation playbook markdown document."""

    slug: str
    class_name: str
    seen: int = 0
    created_from: Literal["success", "recovery"]
    last_updated: str
    summary: str
    applicability: list[str] = Field(default_factory=list)
    mitigation_procedure: list[str] = Field(default_factory=list)
    post_mitigation_verification: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    known_pitfalls: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    markdown: str = ""

    @classmethod
    def parse(cls, text: str) -> MitigationPlaybook:
        """Parse a markdown document into a MitigationPlaybook.

        Raises ``MitigationPlaybookValidationError`` if the document is malformed.
        """
        violations = validate_mitigation_playbook(text)
        if violations:
            raise MitigationPlaybookValidationError(violations)

        meta = extract_meta_block(text)
        assert meta is not None  # validated above

        sections = extract_sections(text)

        created_from_value = meta["created_from"]
        if created_from_value not in ALLOWED_CREATED_FROM:
            raise MitigationPlaybookValidationError([f"meta.created_from: invalid value '{created_from_value}'"])

        return cls(
            slug=meta["slug"],
            class_name=meta["class_name"],
            seen=int(meta["seen"]),
            created_from=cast("Literal['success', 'recovery']", created_from_value),
            last_updated=meta["last_updated"],
            summary=sections.get("Summary", "").strip(),
            applicability=parse_bullet_list(sections.get("Applicability", "")),
            mitigation_procedure=parse_numbered_steps(sections.get("Mitigation Procedure", "")),
            post_mitigation_verification=parse_numbered_steps(sections.get("Post-Mitigation Verification", "")),
            required_evidence=parse_bullet_list(sections.get("Required Evidence", "")),
            known_pitfalls=parse_bullet_list(sections.get("Known Pitfalls", "")),
            failure_patterns=parse_bullet_list(sections.get("Failure Patterns", "")),
            markdown=text,
        )

    def to_markdown(self) -> str:
        """Return a valid markdown string for this mitigation playbook.

        If ``self.markdown`` is non-empty AND parses cleanly, returns it
        verbatim. Otherwise serializes from the structured fields.
        """
        if self.markdown and not validate_mitigation_playbook(self.markdown):
            return self.markdown
        return self._serialize()

    def _serialize(self) -> str:
        lines: list[str] = []
        lines.append(f"# Mitigation Playbook: {self.class_name}")
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
        lines.append("## Applicability")
        lines.extend(f"- {item}" for item in self.applicability)
        lines.append("")
        lines.append("## Mitigation Procedure")
        lines.extend(self.mitigation_procedure)
        lines.append("")
        lines.append("## Post-Mitigation Verification")
        lines.extend(self.post_mitigation_verification)
        lines.append("")
        lines.append("## Required Evidence (Submission Gate)")
        lines.extend(f"- [ ] {item}" for item in self.required_evidence)
        lines.append("")
        lines.append("## Known Pitfalls")
        lines.extend(f"- {item}" for item in self.known_pitfalls)
        lines.append("")
        lines.append("## Failure Patterns")
        lines.extend(f"- {item}" for item in self.failure_patterns)
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mitigation playbook store
# ---------------------------------------------------------------------------


class MitigationPlaybookStore(PlaybookStoreBase):
    """Filesystem-backed store for mitigation playbook markdown files."""

    def _parse(self, text: str) -> MitigationPlaybook:
        return MitigationPlaybook.parse(text)

    def _to_markdown(self, model: MitigationPlaybook) -> str:
        return model.to_markdown()

    def load(self, slug: str) -> MitigationPlaybook | None:  # type: ignore[override]
        return cast("MitigationPlaybook | None", super().load(slug))

    def save(self, playbook: MitigationPlaybook) -> Path:  # type: ignore[override]
        return super().save(playbook)
