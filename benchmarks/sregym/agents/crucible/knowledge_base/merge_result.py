"""Data shapes and helpers for the long-term summary merge envelope.

The Crucible merge step (``_merge_into_long_term_summary``) now returns a
structured ``MergeResult`` instead of a bare string so that downstream
playbook lifecycle handling can decide whether to synthesize, refine, or
consolidate playbooks.

The merge LLM emits a trailing ``<merge_result>...</merge_result>`` JSON
envelope alongside the updated summary text. This module parses that
envelope, validates it against a pre/post snapshot of the summary's slug
landscape, and surfaces structured reorganization declarations.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .playbook import slugify

logger = logging.getLogger(__name__)


MERGE_RESULT_OPEN = "<merge_result>"
MERGE_RESULT_CLOSE = "</merge_result>"

_ROOT_CAUSE_HEADER = re.compile(r"^###\s+Root Cause:\s*(.+?)\s*$")
_SLUG_LINE = re.compile(r"^####\s+Slug:\s*([A-Za-z0-9_]+)\s*$")


ReorgType = Literal["consolidate", "split"]
PrimaryAction = Literal["new", "merged", "noop"]


class MergeEnvelopeError(ValueError):
    """Raised when the merge LLM's <merge_result> envelope is missing or invalid."""


@dataclass(frozen=True)
class Reorganization:
    """One structural change to the slug landscape declared by the merge step."""

    type: ReorgType
    winner_slug: str
    loser_slugs: tuple[str, ...]
    winner_class_name: str | None = None


def _empty_reorgs() -> list[Reorganization]:
    return []


@dataclass
class MergeResult:
    """Outcome of merging a session summary into the long-term summary."""

    primary_action: PrimaryAction
    primary_slug: str | None
    primary_class_name: str | None
    new_summary_text: str
    reorganizations: list[Reorganization] = field(default_factory=_empty_reorgs)


# ---------------------------------------------------------------------------
# Slug extraction and migration
# ---------------------------------------------------------------------------


def extract_class_slugs(summary_text: str) -> dict[str, str]:
    """Return a mapping of ``class_name -> slug`` from ``### Root Cause:`` blocks.

    If a block lacks an explicit ``#### Slug:`` line, the slug is computed
    deterministically via :func:`slugify`. The caller should normally run
    :func:`ensure_slug_lines` first to make slugs explicit in the text.
    """
    lines = summary_text.splitlines()
    class_blocks: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in lines:
        header_match = _ROOT_CAUSE_HEADER.match(line)
        if header_match:
            if current_name is not None:
                class_blocks.append((current_name, current_lines))
            current_name = header_match.group(1).strip()
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)
    if current_name is not None:
        class_blocks.append((current_name, current_lines))

    result: dict[str, str] = {}
    for name, body_lines in class_blocks:
        slug: str | None = None
        for body_line in body_lines:
            slug_match = _SLUG_LINE.match(body_line)
            if slug_match:
                slug = slug_match.group(1).strip()
                break
        if slug is None:
            slug = slugify(name)
        result[name] = slug
    return result


def ensure_slug_lines(summary_text: str) -> str:
    """Inject a ``#### Slug:`` line under any ``### Root Cause:`` block missing one.

    Legacy long-term summaries do not carry slug lines. This migration makes
    slugs explicit so that the LLM can be instructed to preserve them and so
    that :func:`extract_class_slugs` sees the same mapping before and after a
    merge.
    """
    if not summary_text:
        return summary_text

    lines = summary_text.splitlines()
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        header_match = _ROOT_CAUSE_HEADER.match(line)
        if not header_match:
            output.append(line)
            i += 1
            continue

        class_name = header_match.group(1).strip()
        output.append(line)
        i += 1

        block_lines: list[str] = []
        has_slug = False
        while i < len(lines):
            next_line = lines[i]
            if _ROOT_CAUSE_HEADER.match(next_line):
                break
            block_lines.append(next_line)
            if _SLUG_LINE.match(next_line):
                has_slug = True
            i += 1

        if not has_slug:
            slug = slugify(class_name)
            output.append("")
            output.append(f"#### Slug: {slug}")
            start_idx = 0
            while start_idx < len(block_lines) and block_lines[start_idx].strip() == "":
                start_idx += 1
            output.extend(block_lines[start_idx:])
        else:
            output.extend(block_lines)

    return "\n".join(output)


# ---------------------------------------------------------------------------
# Envelope parsing
# ---------------------------------------------------------------------------


def parse_merge_envelope(output_text: str) -> tuple[dict[str, Any], str]:
    """Extract the ``<merge_result>`` JSON envelope from the LLM output.

    Returns ``(envelope_dict, stripped_summary_text)``. Raises
    :class:`MergeEnvelopeError` if the envelope is missing or unparseable.
    """
    open_idx = output_text.rfind(MERGE_RESULT_OPEN)
    close_idx = output_text.rfind(MERGE_RESULT_CLOSE)
    if open_idx == -1 or close_idx == -1 or close_idx < open_idx:
        raise MergeEnvelopeError(f"Missing {MERGE_RESULT_OPEN}...{MERGE_RESULT_CLOSE} block in merge output")
    envelope_str = output_text[open_idx + len(MERGE_RESULT_OPEN) : close_idx].strip()
    try:
        raw = json.loads(envelope_str)
    except json.JSONDecodeError as exc:
        raise MergeEnvelopeError(f"Could not parse merge_result JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise MergeEnvelopeError("merge_result envelope must decode to a JSON object")
    data = cast("dict[str, Any]", raw)

    before = output_text[:open_idx].rstrip()
    after = output_text[close_idx + len(MERGE_RESULT_CLOSE) :].strip()
    stripped = (before + "\n" + after).strip() if after else before
    return data, stripped


def build_merge_result(
    envelope: dict[str, Any],
    pre_merge_slugs: dict[str, str],
    post_merge_slugs: dict[str, str],
    new_summary_text: str,
) -> MergeResult:
    """Turn a parsed envelope into a :class:`MergeResult`, validating against snapshots."""
    raw_action = envelope.get("primary_action")
    if raw_action not in ("new", "merged", "noop"):
        raise MergeEnvelopeError(f"primary_action must be one of new|merged|noop, got {raw_action!r}")
    primary_action: PrimaryAction = raw_action  # type: ignore[assignment]

    primary_class_name_raw = envelope.get("primary_class_name")
    primary_class_name: str | None
    if primary_class_name_raw in (None, ""):
        primary_class_name = None
    elif isinstance(primary_class_name_raw, str):
        primary_class_name = primary_class_name_raw.strip() or None
    else:
        raise MergeEnvelopeError(
            f"primary_class_name must be a string or null, got {type(primary_class_name_raw).__name__}"
        )

    if primary_action in ("new", "merged") and not primary_class_name:
        raise MergeEnvelopeError(f"primary_action={primary_action} requires a non-empty primary_class_name")

    primary_slug: str | None = None
    if primary_class_name is not None:
        primary_slug = post_merge_slugs.get(primary_class_name)
        if primary_slug is None:
            primary_slug = pre_merge_slugs.get(primary_class_name)
        if primary_slug is None:
            primary_slug = slugify(primary_class_name)

    reorgs_raw_obj: Any = envelope.get("reorganizations") or []
    if not isinstance(reorgs_raw_obj, list):
        raise MergeEnvelopeError("reorganizations must be a list")
    reorgs_raw = cast("list[Any]", reorgs_raw_obj)

    declared_loser_slugs: set[str] = set()
    reorgs: list[Reorganization] = []
    for entry_obj in reorgs_raw:
        if not isinstance(entry_obj, dict):
            raise MergeEnvelopeError(f"reorganization entry must be an object, got {type(entry_obj).__name__}")
        entry = cast("dict[str, Any]", entry_obj)
        rtype: Any = entry.get("type")
        if rtype == "consolidate":
            winner_name_raw: Any = entry.get("winner", "")
            if not isinstance(winner_name_raw, str) or not winner_name_raw.strip():
                raise MergeEnvelopeError("consolidate.winner must be a non-empty string")
            winner_name = winner_name_raw.strip()
            losers_obj: Any = entry.get("losers") or []
            if not isinstance(losers_obj, list) or not losers_obj:
                raise MergeEnvelopeError("consolidate.losers must be a non-empty list")
            losers_raw = cast("list[Any]", losers_obj)
            loser_names = [x.strip() for x in losers_raw if isinstance(x, str) and x.strip()]
            if not loser_names:
                raise MergeEnvelopeError("consolidate.losers must contain at least one non-empty name")

            winner_slug = post_merge_slugs.get(winner_name) or pre_merge_slugs.get(winner_name) or slugify(winner_name)
            loser_slugs: list[str] = []
            for loser_name in loser_names:
                slug = pre_merge_slugs.get(loser_name) or slugify(loser_name)
                loser_slugs.append(slug)
                declared_loser_slugs.add(slug)
            reorgs.append(
                Reorganization(
                    type="consolidate",
                    winner_slug=winner_slug,
                    loser_slugs=tuple(loser_slugs),
                    winner_class_name=winner_name,
                )
            )
        elif rtype == "split":
            logger.warning(
                "Split reorganization declared — v1 does not fully support splits: %s",
                entry,
            )
            parent_name_raw: Any = entry.get("from", "")
            if not isinstance(parent_name_raw, str) or not parent_name_raw.strip():
                raise MergeEnvelopeError("split.from must be a non-empty string")
            parent_name = parent_name_raw.strip()
            children_obj: Any = entry.get("to") or []
            if not isinstance(children_obj, list):
                raise MergeEnvelopeError("split.to must be a list")
            children_raw = cast("list[Any]", children_obj)
            child_names = [x.strip() for x in children_raw if isinstance(x, str) and x.strip()]
            parent_slug = pre_merge_slugs.get(parent_name) or slugify(parent_name)
            child_slugs = tuple(post_merge_slugs.get(c) or slugify(c) for c in child_names)
            reorgs.append(
                Reorganization(
                    type="split",
                    winner_slug=parent_slug,
                    loser_slugs=child_slugs,
                )
            )
        else:
            raise MergeEnvelopeError(f"Unknown reorganization type: {rtype!r}")

    pre_slug_set = set(pre_merge_slugs.values())
    post_slug_set = set(post_merge_slugs.values())
    undeclared_destroyed = (pre_slug_set - post_slug_set) - declared_loser_slugs
    if undeclared_destroyed:
        raise MergeEnvelopeError(
            "Summary destroyed slugs "
            f"{sorted(undeclared_destroyed)} without declaring them as consolidation "
            "losers. Declare a reorganization or preserve the entries."
        )

    return MergeResult(
        primary_action=primary_action,
        primary_slug=primary_slug,
        primary_class_name=primary_class_name,
        new_summary_text=new_summary_text,
        reorganizations=reorgs,
    )
