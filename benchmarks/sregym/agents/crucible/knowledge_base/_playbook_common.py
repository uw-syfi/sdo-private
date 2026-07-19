"""Shared parsing, validation, and storage primitives for playbook variants.

The Crucible agent stores two flavors of per-class markdown documents:

* Diagnosis playbooks (``playbook.py``) — triage and verification procedures
  used by the LTM verification subagents.
* Mitigation playbooks (``mitigation_playbook.py``) — fix-and-verify
  procedures used by the LTM mitigation subagents.

Both flavors share the same ``<!-- meta -->`` block format, the same section
parser, the same vague-phrase blacklist, the same slug derivation, and the
same alias/diff/history machinery on disk. Those primitives live here so the
two model files only need to declare their section list and the model-specific
parsing logic.
"""

from __future__ import annotations

import difflib
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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


def extract_meta_block(text: str) -> dict[str, str] | None:
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


def normalize_section_title(title: str) -> str:
    """Drop any trailing parenthesized qualifier from a section title.

    For example, ``"Required Evidence (Submission Gate)"`` becomes
    ``"Required Evidence"``.
    """
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def extract_sections(text: str) -> dict[str, str]:
    """Split markdown into a mapping of section title -> raw body text.

    Sections are keyed by their normalized title (trailing parenthesized
    qualifiers stripped) so callers can look up ``"Required Evidence"`` even
    if the document uses ``"Required Evidence (Submission Gate)"``.
    """
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        header_match = _SECTION_HEADER.match(line)
        if header_match:
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip("\n")
            current_title = normalize_section_title(header_match.group(1).strip())
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        sections[current_title] = "\n".join(current_lines).strip("\n")

    return sections


def parse_bullet_list(body: str) -> list[str]:
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


def parse_numbered_steps(body: str) -> list[str]:
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
# Validation helpers
# ---------------------------------------------------------------------------


def validate_meta_block(meta: dict[str, str] | None) -> list[str]:
    """Return violations for the meta block (missing keys, malformed values)."""
    violations: list[str] = []
    if meta is None:
        violations.append("meta: missing or malformed <!-- meta --> block")
        return violations
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
    return violations


def validate_vague_phrases(text: str) -> list[str]:
    """Return one violation per occurrence of any forbidden vague phrase."""
    violations: list[str] = []
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
# Store base class
# ---------------------------------------------------------------------------


class PlaybookStoreBase:
    """Filesystem-backed store machinery shared between playbook variants.

    Subclasses define ``_parse(text)`` (returning the model class) and
    ``_to_markdown(model)`` (serialization). All directory layout, alias
    handling, history diff capture, and consolidation archival lives here.
    """

    def __init__(self, playbooks_dir: Path):
        self.playbooks_dir = Path(playbooks_dir)

    # -- subclass hooks ----------------------------------------------------

    def _parse(self, text: str) -> Any:
        raise NotImplementedError

    def _to_markdown(self, model: Any) -> str:
        raise NotImplementedError

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

    def load(self, slug: str) -> Any:
        """Load a model by slug, following alias chains. Returns ``None`` on miss."""
        resolved = self.resolve_alias(slug)
        path = self._playbook_path(resolved)
        if not path.exists():
            return None
        return self._parse(path.read_text())

    def save(self, model: Any) -> Path:
        """Persist a model, recording a unified diff if a prior exists."""
        self._ensure_dirs()
        slug: str = model.slug
        path = self._playbook_path(slug)
        new_text = self._to_markdown(model)

        if path.exists():
            old_text = path.read_text()
            if old_text != new_text:
                self._write_diff(slug, old_text, new_text)

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
        """Return a sorted list of stored slugs."""
        if not self.playbooks_dir.is_dir():
            return []
        return sorted(p.stem for p in self.playbooks_dir.glob("*.md") if p.is_file())
