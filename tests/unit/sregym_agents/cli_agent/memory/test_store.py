"""Unit tests for the per-app JSONL lesson store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sregym_agents.cli_agent.memory.store import (
    CONFIRMED_VERDICT,
    Lesson,
    LessonStore,
    slugify,
)

if TYPE_CHECKING:
    from pathlib import Path


def _append_sample(store: LessonStore, app: str, *, root_cause: str = "missing DB_HOST") -> Lesson:
    return store.append(
        app,
        situation="frontend 503s; profile pod CrashLoopBackOff",
        root_cause=root_cause,
        tell="kubectl describe shows env unset",
        fix="set DB_HOST on profile deployment env",
        affected_resource="deployment/profile.env",
        confirmed_by=CONFIRMED_VERDICT,
        obvious_guess="bad image tag",
    )


def test_slugify_normalizes_app_names() -> None:
    assert slugify("hotelReservation") == "hotelreservation"
    assert slugify("astronomy-shop") == "astronomy-shop"
    assert slugify("Social Network!") == "social-network"
    assert slugify("") == "unknown"


def test_load_missing_store_is_empty(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    assert store.load("hotelReservation") == []


def test_append_then_load_round_trip(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    lesson = _append_sample(store, "hotelReservation")

    assert lesson.id == 1
    assert lesson.seen_count == 1
    assert lesson.created_at  # stamped

    loaded = store.load("hotelReservation")
    assert len(loaded) == 1
    assert loaded[0] == lesson


def test_append_assigns_increasing_ids(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    first = _append_sample(store, "hotelReservation", root_cause="missing DB_HOST")
    second = _append_sample(store, "hotelReservation", root_cause="bad replica count")
    assert (first.id, second.id) == (1, 2)
    assert len(store.load("hotelReservation")) == 2


def test_per_app_isolation(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    _append_sample(store, "hotelReservation")
    _append_sample(store, "socialNetwork")
    assert len(store.load("hotelReservation")) == 1
    assert len(store.load("socialNetwork")) == 1
    # Separate files on disk.
    assert store.path_for("hotelReservation") != store.path_for("socialNetwork")


def test_merge_increments_seen_count_and_broadens(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    original = _append_sample(store, "hotelReservation")

    merged = store.merge(
        "hotelReservation",
        original.id,
        situation="frontend 503s OR 500s; profile pod CrashLoopBackOff or not ready",
        root_cause="missing DB_HOST env var on profile deployment",
        tell="kubectl describe shows env unset; logs show DNS resolve fail",
        fix="set DB_HOST on profile deployment env",
        affected_resource="deployment/profile.env",
    )

    assert merged.id == original.id
    assert merged.seen_count == 2
    assert "500s" in merged.situation
    assert merged.created_at == original.created_at  # preserved

    loaded = store.load("hotelReservation")
    assert len(loaded) == 1  # merged in place, not appended
    assert loaded[0].seen_count == 2


def test_merge_unknown_id_raises(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    _append_sample(store, "hotelReservation")
    with pytest.raises(KeyError):
        store.merge(
            "hotelReservation",
            999,
            situation="x",
            root_cause="y",
            tell="z",
            fix="w",
            affected_resource="r",
        )


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    good = _append_sample(store, "hotelReservation")
    # Corrupt the file with a junk line plus a blank line.
    path = store.path_for("hotelReservation")
    path.write_text(path.read_text() + "not json\n\n", encoding="utf-8")

    loaded = store.load("hotelReservation")
    assert loaded == [good]


def _concurrent_append(args: tuple[str, int]) -> None:
    store_dir, n = args
    store = LessonStore(store_dir)
    store.append(
        "hotelReservation",
        situation=f"sit {n}",
        root_cause=f"cause {n}",
        tell="t",
        fix="f",
        affected_resource=f"deployment/x{n}",
        confirmed_by=CONFIRMED_VERDICT,
    )


def test_concurrent_appends_do_not_lose_updates(tmp_path: Path) -> None:
    """Parallel workers (separate processes) appending to one per-app file must
    not lose updates — the flock serializes the read-modify-write."""
    from concurrent.futures import ProcessPoolExecutor

    n = 12
    with ProcessPoolExecutor(max_workers=4) as ex:
        list(ex.map(_concurrent_append, [(str(tmp_path), i) for i in range(n)]))

    lessons = LessonStore(tmp_path).load("hotelReservation")
    assert len(lessons) == n  # nothing lost
    assert len({lesson.id for lesson in lessons}) == n  # ids unique, no clobber


def test_from_dict_tolerates_unknown_keys() -> None:
    lesson = Lesson.from_dict(
        {
            "id": 5,
            "app": "hotelReservation",
            "situation": "s",
            "root_cause": "rc",
            "tell": "t",
            "fix": "f",
            "affected_resource": "ar",
            "confirmed_by": CONFIRMED_VERDICT,
            "seen_count": 3,
            "role": "db-dependent-startup",  # future field, must be ignored
        }
    )
    assert lesson.id == 5
    assert lesson.seen_count == 3
    assert not hasattr(lesson, "role")
