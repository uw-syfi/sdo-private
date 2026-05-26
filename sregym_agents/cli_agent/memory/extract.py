"""Lesson extraction + dedup via a resumed agent turn.

The driver, after the conductor confirms a problem, issues a *second*
``generate`` on the same agent session (which still holds the full
investigation in its context window). This module builds that prompt and
parses the reply into an :class:`UpsertResult` — either a brand-new lesson
or a merge into an existing one. The agent does extraction (distil the
transcript into the §4 schema) and dedup (compare against the existing
per-app lessons) in that single turn.

No LLM client lives here: the driver feeds ``build_extraction_prompt`` to
``session.generate`` and passes the reply to ``parse_upsert``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .store import Lesson, LessonStore

logger = logging.getLogger(__name__)

# What the agent should emit. confirmed_by is supplied by the driver (it
# knows the verdict), so the model is not asked for it.
_NEW_KEYS = ("situation", "root_cause", "tell", "fix", "affected_resource")

_PROMPT = """\
The incident is now resolved and the outcome has been confirmed by the \
environment ({confirmation_note}). Record one durable LESSON from this \
investigation so a future run facing a similar situation can move faster.

Capture the *surprise*, not the prior: where reality contradicted the \
obvious first guess, the discriminating tell that revealed the true cause, \
and the confirmed root cause and fix. Keep each field to one or two \
sentences of plain text.

{dedup_section}

Reply with a SINGLE JSON object and nothing else — no prose, no markdown \
fences. Do NOT call any tools (do not call submit or recall again). Schema:

{{
  "decision": "new" | "merge",
  "merge_into": <id of the lesson to merge into, or null for a new lesson>,
  "situation": "what was observed, including what changed (rollout/deploy) if known",
  "obvious_guess": "the tempting-but-wrong first guess, or empty string",
  "root_cause": "the confirmed root cause",
  "tell": "the check/output that discriminated the true cause from the guess",
  "fix": "the action that resolved it",
  "affected_resource": "the resource the fix touched, e.g. deployment/profile.env"
}}

If this incident matches an existing lesson's root cause, set \
"decision":"merge" and "merge_into" to its id, and BROADEN situation/tell to \
also cover this presentation. When unsure, prefer "decision":"new" — a \
duplicate is harmless, but a wrong merge corrupts a good lesson.\
"""

_NO_LESSONS = "There are no existing lessons for this app yet, so this must be a new lesson."

_EXISTING_HEADER = "Existing lessons for this app (compare against their root causes to decide new vs. merge):"

_CONFIRMATION_NOTES = {
    "verdict": "the grader confirmed the diagnosis and fix",
    "self_verified": "the symptoms cleared after the fix",
    "diagnosis_only": "the diagnosis was confirmed but the fix was NOT confirmed — "
    "record the fix you attempted but treat it as unverified",
}


@dataclass
class UpsertResult:
    """Parsed extraction reply: a new lesson or a merge into ``merge_into``."""

    decision: str  # "new" | "merge"
    situation: str
    root_cause: str
    tell: str
    fix: str
    affected_resource: str
    obvious_guess: str = ""
    merge_into: int | None = None


def build_extraction_prompt(existing: list[Lesson], confirmed_by: str) -> str:
    """Render the resume-turn prompt given the app's existing lessons."""
    if existing:
        lines = [_EXISTING_HEADER]
        lines.extend(
            f"  - id={lesson.id} (seen {lesson.seen_count}x): "
            f"root_cause={lesson.root_cause!r}; "
            f"affected_resource={lesson.affected_resource!r}; "
            f"situation={lesson.situation!r}"
            for lesson in existing
        )
        dedup_section = "\n".join(lines)
    else:
        dedup_section = _NO_LESSONS
    note = _CONFIRMATION_NOTES.get(confirmed_by, "the outcome was confirmed")
    return _PROMPT.format(confirmation_note=note, dedup_section=dedup_section)


def _extract_json_object(reply: str) -> dict[str, Any] | None:
    """Pull the first top-level JSON object out of a model reply.

    Tolerates markdown fences and surrounding prose by scanning for the first
    balanced ``{...}`` block.
    """
    text = reply.strip()
    # Strip a leading ```json / ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return cast("dict[str, Any]", obj) if isinstance(obj, dict) else None
    return None


def parse_upsert(reply: str, existing: list[Lesson]) -> UpsertResult | None:
    """Parse a resume-turn reply into an UpsertResult, or None to skip the write.

    Returns None (and logs) on unparseable JSON, missing required fields, or a
    ``merge`` decision whose ``merge_into`` is not an existing lesson id — a
    bad write is worse than no write.
    """
    obj = _extract_json_object(reply)
    if obj is None:
        logger.warning("Lesson extraction: no JSON object in reply; skipping write")
        return None
    missing = [k for k in _NEW_KEYS if not str(obj.get(k, "")).strip()]
    if missing:
        logger.warning("Lesson extraction: missing required fields %s; skipping write", missing)
        return None

    decision = str(obj.get("decision", "new")).strip().lower()
    merge_into = obj.get("merge_into")
    if decision == "merge":
        valid_ids = {lesson.id for lesson in existing}
        if not isinstance(merge_into, int) or merge_into not in valid_ids:
            logger.warning(
                "Lesson extraction: merge into invalid id %r; storing as new instead",
                merge_into,
            )
            decision, merge_into = "new", None
    else:
        decision, merge_into = "new", None

    return UpsertResult(
        decision=decision,
        merge_into=merge_into,
        situation=str(obj["situation"]).strip(),
        obvious_guess=str(obj.get("obvious_guess", "")).strip(),
        root_cause=str(obj["root_cause"]).strip(),
        tell=str(obj["tell"]).strip(),
        fix=str(obj["fix"]).strip(),
        affected_resource=str(obj["affected_resource"]).strip(),
    )


def apply_upsert(store: LessonStore, app: str, result: UpsertResult, confirmed_by: str) -> Lesson:
    """Write ``result`` to the store: merge into an existing lesson or append a new one."""
    if result.decision == "merge" and result.merge_into is not None:
        return store.merge(
            app,
            result.merge_into,
            situation=result.situation,
            root_cause=result.root_cause,
            tell=result.tell,
            fix=result.fix,
            affected_resource=result.affected_resource,
            obvious_guess=result.obvious_guess,
        )
    return store.append(
        app,
        situation=result.situation,
        root_cause=result.root_cause,
        tell=result.tell,
        fix=result.fix,
        affected_resource=result.affected_resource,
        confirmed_by=confirmed_by,
        obvious_guess=result.obvious_guess,
    )
