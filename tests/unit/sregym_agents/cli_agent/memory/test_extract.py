"""Unit tests for lesson extraction prompt + reply parsing."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sregym_agents.cli_agent.memory.extract import (
    apply_upsert,
    build_extraction_prompt,
    parse_upsert,
)
from sregym_agents.cli_agent.memory.store import CONFIRMED_VERDICT, Lesson, LessonStore

if TYPE_CHECKING:
    from pathlib import Path


def _lesson(lid: int, root_cause: str) -> Lesson:
    return Lesson(
        id=lid,
        app="hotelReservation",
        situation="frontend 503s",
        root_cause=root_cause,
        tell="env unset",
        fix="set env",
        affected_resource="deployment/profile.env",
        confirmed_by=CONFIRMED_VERDICT,
    )


_NEW_REPLY = json.dumps(
    {
        "decision": "new",
        "merge_into": None,
        "situation": "frontend 503s; profile CrashLoopBackOff",
        "obvious_guess": "bad image tag",
        "root_cause": "missing DB_HOST env var",
        "tell": "kubectl describe shows env unset",
        "fix": "set DB_HOST on profile deployment",
        "affected_resource": "deployment/profile.env",
    }
)


# --- prompt ---------------------------------------------------------------


def test_prompt_no_existing_lessons_says_new() -> None:
    prompt = build_extraction_prompt([], CONFIRMED_VERDICT)
    assert "no existing lessons" in prompt.lower()
    assert "do not call" in prompt.lower()  # don't re-call submit/recall


def test_prompt_lists_existing_lessons_for_dedup() -> None:
    prompt = build_extraction_prompt([_lesson(1, "missing DB_HOST")], CONFIRMED_VERDICT)
    assert "id=1" in prompt
    assert "missing DB_HOST" in prompt


def test_prompt_diagnosis_only_flags_unverified_fix() -> None:
    prompt = build_extraction_prompt([], "diagnosis_only")
    assert "not confirmed" in prompt.lower() or "unverified" in prompt.lower()


# --- parsing --------------------------------------------------------------


def test_parse_new_lesson() -> None:
    result = parse_upsert(_NEW_REPLY, [])
    assert result is not None
    assert result.decision == "new"
    assert result.merge_into is None
    assert result.root_cause == "missing DB_HOST env var"
    assert result.obvious_guess == "bad image tag"


def test_parse_strips_markdown_fences_and_prose() -> None:
    reply = f"Here is the lesson:\n```json\n{_NEW_REPLY}\n```\nDone."
    result = parse_upsert(reply, [])
    assert result is not None
    assert result.decision == "new"


def test_parse_merge_with_valid_id() -> None:
    reply = json.dumps(
        {
            "decision": "merge",
            "merge_into": 2,
            "situation": "broadened",
            "root_cause": "missing DB_HOST",
            "tell": "env unset",
            "fix": "set env",
            "affected_resource": "deployment/profile.env",
        }
    )
    result = parse_upsert(reply, [_lesson(2, "missing DB_HOST")])
    assert result is not None
    assert result.decision == "merge"
    assert result.merge_into == 2


def test_parse_merge_into_unknown_id_falls_back_to_new() -> None:
    reply = json.dumps(
        {
            "decision": "merge",
            "merge_into": 99,
            "situation": "s",
            "root_cause": "rc",
            "tell": "t",
            "fix": "f",
            "affected_resource": "ar",
        }
    )
    result = parse_upsert(reply, [_lesson(1, "x")])
    assert result is not None
    assert result.decision == "new"
    assert result.merge_into is None


def test_parse_missing_required_field_skips() -> None:
    reply = json.dumps({"decision": "new", "situation": "", "root_cause": "rc"})
    assert parse_upsert(reply, []) is None


def test_parse_non_json_skips() -> None:
    assert parse_upsert("I could not produce a lesson.", []) is None


# --- apply ----------------------------------------------------------------


def test_apply_new_appends(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    result = parse_upsert(_NEW_REPLY, [])
    assert result is not None
    lesson = apply_upsert(store, "hotelReservation", result, CONFIRMED_VERDICT)
    assert lesson.id == 1
    assert lesson.confirmed_by == CONFIRMED_VERDICT
    assert len(store.load("hotelReservation")) == 1


def test_apply_merge_increments_seen_count(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    original = store.append(
        "hotelReservation",
        situation="frontend 503s",
        root_cause="missing DB_HOST",
        tell="env unset",
        fix="set env",
        affected_resource="deployment/profile.env",
        confirmed_by=CONFIRMED_VERDICT,
    )
    reply = json.dumps(
        {
            "decision": "merge",
            "merge_into": original.id,
            "situation": "frontend 503s OR 500s",
            "root_cause": "missing DB_HOST",
            "tell": "env unset; DNS fail",
            "fix": "set env",
            "affected_resource": "deployment/profile.env",
        }
    )
    result = parse_upsert(reply, [original])
    assert result is not None
    merged = apply_upsert(store, "hotelReservation", result, CONFIRMED_VERDICT)
    assert merged.seen_count == 2
    assert len(store.load("hotelReservation")) == 1
