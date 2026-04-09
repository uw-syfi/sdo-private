"""Playbook data layer: parser, validator, store, and slug helper.

Playbooks are per-root-cause-class markdown documents containing concrete
triage and verification procedures that the Crucible diagnosis agent reads
at runtime. This module provides the foundational data shapes and storage,
without any LLM synthesis logic.
"""

from __future__ import annotations

import difflib
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field

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

REQUIRED_META_KEYS: tuple[str, ...] = (
    "slug",
    "class_name",
    "seen",
    "created_from",
    "last_updated",
)

ALLOWED_CREATED_FROM: tuple[str, ...] = ("success", "recovery")

VAGUE_PHRASES: tuple[str, ...] = (
    "appropriate values",
    "as needed",
    "relevant configuration",
    "if applicable",
    "as appropriate",
)

META_OPEN = "<!-- meta -->"
META_CLOSE = "<!-- /meta -->"

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SECTION_HEADER = re.compile(r"^##\s+(.+?)\s*$")
_STEP_START = re.compile(r"^(\d+)\.\s")


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
# Slug helper
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Derive a deterministic slug from a free-form class name."""
    lowered = name.lower()
    replaced = _SLUG_NON_ALNUM.sub("_", lowered)
    stripped = replaced.strip("_")
    return stripped[:64]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_meta_block(text: str) -> dict[str, str] | None:
    """Return the parsed meta key/value mapping, or None if absent/malformed."""
    if META_OPEN not in text or META_CLOSE not in text:
        return None
    start = text.index(META_OPEN) + len(META_OPEN)
    end = text.index(META_CLOSE)
    if end <= start:
        return None
    block = text[start:end]
    meta: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def _normalize_section_title(title: str) -> str:
    """Drop any trailing parenthesized qualifier from a section title.

    For example, ``"Required Evidence (Submission Gate)"`` becomes
    ``"Required Evidence"``.
    """
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def _extract_sections(text: str) -> dict[str, str]:
    """Split markdown into a mapping of section title -> raw body text.

    Sections are keyed by their normalized title (trailing parenthesized
    qualifiers stripped) so callers can look up ``"Required Evidence"``
    even if the document uses ``"Required Evidence (Submission Gate)"``.
    """
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        header_match = _SECTION_HEADER.match(line)
        if header_match:
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip("\n")
            current_title = _normalize_section_title(header_match.group(1).strip())
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        sections[current_title] = "\n".join(current_lines).strip("\n")

    return sections


def _parse_bullet_list(body: str) -> list[str]:
    """Parse lines beginning with ``- `` (or ``- [ ]``) as bullet items."""
    items: list[str] = []
    current: list[str] | None = None

    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- [ ]"):
            if current is not None:
                items.append(" ".join(current).strip())
            content = stripped[len("- [ ]") :].strip()
            current = [content] if content else []
        elif stripped.startswith("- "):
            if current is not None:
                items.append(" ".join(current).strip())
            content = stripped[2:].strip()
            current = [content] if content else []
        else:
            if current is not None and stripped:
                current.append(stripped)

    if current is not None:
        items.append(" ".join(current).strip())

    return [item for item in items if item]


def _parse_numbered_steps(body: str) -> list[str]:
    """Parse a numbered procedure block into a list of full step texts.

    Each step starts with ``<n>.`` at column 0 and may span multiple lines
    (continuation lines are indented). The full step text is concatenated
    with newlines preserved so structured sub-fields like ``**Action:**``
    remain intact.
    """
    steps: list[str] = []
    current: list[str] | None = None

    for line in body.splitlines():
        if _STEP_START.match(line):
            if current is not None:
                joined = "\n".join(current).strip()
                if joined:
                    steps.append(joined)
            current = [line]
        else:
            if current is not None:
                current.append(line)

    if current is not None:
        joined = "\n".join(current).strip()
        if joined:
            steps.append(joined)

    return steps


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_playbook(text: str) -> list[str]:
    """Return a list of human-readable violations. Empty list means valid."""
    violations: list[str] = []

    meta = _extract_meta_block(text)
    if meta is None:
        violations.append("meta: missing or malformed <!-- meta --> block")
    else:
        violations.extend(
            f"meta: missing required key '{key}'" for key in REQUIRED_META_KEYS if key not in meta or not meta[key]
        )
        if "seen" in meta and meta["seen"]:
            try:
                int(meta["seen"])
            except ValueError:
                violations.append(f"meta.seen: not an integer ('{meta['seen']}')")
        if "created_from" in meta and meta["created_from"] and meta["created_from"] not in ALLOWED_CREATED_FROM:
            violations.append(
                f"meta.created_from: must be one of {list(ALLOWED_CREATED_FROM)}, got '{meta['created_from']}'"
            )

    sections = _extract_sections(text)
    violations.extend(
        f"section: missing required '## {required}'" for required in REQUIRED_SECTIONS if required not in sections
    )

    if "Symptoms" in sections and not _parse_bullet_list(sections["Symptoms"]):
        violations.append("Symptoms: must contain at least one '- ' bullet")

    if "Triage Procedure" in sections and not _parse_numbered_steps(sections["Triage Procedure"]):
        violations.append("Triage Procedure: must contain at least one numbered step")

    if "Verification Procedure" in sections and not _parse_numbered_steps(sections["Verification Procedure"]):
        violations.append("Verification Procedure: must contain at least one numbered step")

    if "Required Evidence" in sections:
        body = sections["Required Evidence"]
        items = _parse_bullet_list(body)
        if not items:
            violations.append("Required Evidence: must contain at least one item")
        has_checkbox = any(line.lstrip().startswith("- [ ]") for line in body.splitlines())
        if not has_checkbox:
            violations.append("Required Evidence: must contain at least one '- [ ]' checkbox item")

    lowered = text.lower()
    for phrase in VAGUE_PHRASES:
        idx = 0
        while True:
            found = lowered.find(phrase, idx)
            if found == -1:
                break
            violations.append(f"forbidden vague phrase: '{phrase}'")
            idx = found + len(phrase)

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

        meta = _extract_meta_block(text)
        assert meta is not None  # validated above

        sections = _extract_sections(text)

        created_from_value = meta["created_from"]
        if created_from_value not in ALLOWED_CREATED_FROM:
            # Defensive: validator should have caught this.
            raise PlaybookValidationError([f"meta.created_from: invalid value '{created_from_value}'"])

        return cls(
            slug=meta["slug"],
            class_name=meta["class_name"],
            seen=int(meta["seen"]),
            created_from=created_from_value,  # type: ignore[arg-type]
            last_updated=meta["last_updated"],
            summary=sections.get("Summary", "").strip(),
            symptoms=_parse_bullet_list(sections.get("Symptoms", "")),
            triage_procedure=_parse_numbered_steps(sections.get("Triage Procedure", "")),
            verification_procedure=_parse_numbered_steps(sections.get("Verification Procedure", "")),
            required_evidence=_parse_bullet_list(sections.get("Required Evidence", "")),
            known_distractors=_parse_bullet_list(sections.get("Known Distractors", "")),
            failure_patterns=_parse_bullet_list(sections.get("Failure Patterns", "")),
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


class PlaybookStore:
    """Filesystem-backed store for playbook markdown files."""

    def __init__(self, playbooks_dir: Path):
        self.playbooks_dir = Path(playbooks_dir)

    # -- path helpers ------------------------------------------------------

    def _ensure_dirs(self) -> None:
        self.playbooks_dir.mkdir(parents=True, exist_ok=True)
        (self.playbooks_dir / ".history").mkdir(parents=True, exist_ok=True)

    def _playbook_path(self, slug: str) -> Path:
        return self.playbooks_dir / f"{slug}.md"

    def _aliases_path(self) -> Path:
        return self.playbooks_dir / ".aliases.yaml"

    def _history_dir(self, slug: str) -> Path:
        return self.playbooks_dir / ".history" / slug

    # -- alias handling ----------------------------------------------------

    def _load_aliases(self) -> dict[str, str]:
        path = self._aliases_path()
        if not path.exists():
            return {}
        try:
            raw: Any = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            logger.warning(f"Failed to parse aliases file {path}: {exc}")
            return {}
        if not isinstance(raw, dict):
            return {}
        data = cast("dict[Any, Any]", raw)
        return {str(k): str(v) for k, v in data.items()}

    def _write_aliases(self, aliases: dict[str, str]) -> None:
        self._ensure_dirs()
        path = self._aliases_path()
        path.write_text(yaml.safe_dump(aliases, sort_keys=True))

    def _resolve_with_map(self, slug: str, aliases: dict[str, str]) -> str:
        seen: set[str] = set()
        current = slug
        while current in aliases:
            if current in seen:
                raise RuntimeError(f"Alias cycle detected involving '{current}'")
            seen.add(current)
            current = aliases[current]
        return current

    def resolve_alias(self, slug: str) -> str:
        """Resolve an alias chain to the canonical slug. Cycle-guarded."""
        aliases = self._load_aliases()
        return self._resolve_with_map(slug, aliases)

    def add_alias(self, from_slug: str, to_slug: str) -> None:
        """Register ``from_slug -> to_slug`` and collapse transitive aliases."""
        aliases = self._load_aliases()
        aliases[from_slug] = to_slug

        # Re-point any aliases that previously pointed at from_slug.
        for key, target in list(aliases.items()):
            if target == from_slug and key != from_slug:
                aliases[key] = to_slug

        # Cycle-check by resolving every alias.
        for key in aliases:
            self._resolve_with_map(key, aliases)

        self._write_aliases(aliases)

    # -- core operations ---------------------------------------------------

    def load(self, slug: str) -> Playbook | None:
        """Load a playbook by slug, following alias chains."""
        resolved = self.resolve_alias(slug)
        path = self._playbook_path(resolved)
        if not path.exists():
            return None
        return Playbook.parse(path.read_text())

    def save(self, playbook: Playbook) -> Path:
        """Persist a playbook, recording a unified diff if a prior exists."""
        self._ensure_dirs()
        path = self._playbook_path(playbook.slug)
        new_text = playbook.to_markdown()

        if path.exists():
            old_text = path.read_text()
            if old_text != new_text:
                self._write_diff(playbook.slug, old_text, new_text)

        path.write_text(new_text)
        logger.info(f"Saved playbook to {path}")
        return path

    def _write_diff(self, slug: str, old_text: str, new_text: str) -> None:
        history_dir = self._history_dir(slug)
        history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        diff_path = history_dir / f"{timestamp}.diff"
        diff_lines = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"{slug}.md (prior)",
            tofile=f"{slug}.md (new)",
        )
        diff_path.write_text("".join(diff_lines))
        logger.info(f"Recorded playbook diff at {diff_path}")

    def archive_consolidation(self, loser_slug: str, winner_slug: str) -> Path | None:
        """Move a consolidated playbook into history. Returns destination path."""
        src = self._playbook_path(loser_slug)
        if not src.exists():
            return None
        history_dir = self._history_dir(loser_slug)
        history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = history_dir / f"consolidated_into_{winner_slug}_{timestamp}.md"
        shutil.move(str(src), str(dest))
        logger.info(f"Archived consolidated playbook {src.name} -> {dest}")
        return dest

    def list_slugs(self) -> list[str]:
        """Return a sorted list of stored playbook slugs."""
        if not self.playbooks_dir.is_dir():
            return []
        return sorted(p.stem for p in self.playbooks_dir.glob("*.md") if p.is_file())
