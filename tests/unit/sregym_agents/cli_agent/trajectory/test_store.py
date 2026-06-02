"""Unit tests for the JSONL trajectory store + recorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sregym_agents.cli_agent.trajectory.store import (
    TrajectoryMeta,
    TrajectoryRecorder,
    TrajectoryStore,
)

if TYPE_CHECKING:
    from pathlib import Path


def _meta(problem: str = "wrong_service_selector_demo", ts: str = "20260601_000000") -> TrajectoryMeta:
    return TrajectoryMeta(
        app="hotelReservation", problem_id=problem, ts=ts, provider="claude", model="sonnet", task="frontend 503s"
    )


def test_recorder_captures_events_and_digest(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    rec = TrajectoryRecorder(writer)

    rec.on_thinking("looking at pods")
    rec.on_tool_call("bash", {"command": "kubectl get pods"})
    rec.on_tool_result("bash", stdout="profile CrashLoopBackOff", exit_code=0)
    rec.on_usage({"input_tokens": 10, "output_tokens": 5})
    writer.close(outcome="completed")

    digest = store.digest(writer.path)
    assert digest is not None
    assert digest.app == "hotelReservation"
    assert digest.run_id  # non-identifying hash, assigned by the store
    assert digest.outcome == "completed"
    assert digest.tool_calls == ["bash"]
    assert "looking at pods" in digest.text  # the agent's own narration is the signal


def test_problem_id_does_not_leak_into_file_or_digest(tmp_path: Path) -> None:
    """The answer-leaking problem_id / provider / model must never reach the
    agent-readable file, filename, or digest — only the sidecar index."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta(problem="wrong_service_selector_hotel_reservation"))
    TrajectoryRecorder(writer).on_thinking("investigating endpoints")
    writer.close(outcome="completed")

    # Filename is the run_id hash, not the problem slug.
    assert "wrong_service_selector" not in writer.path.name
    # File contents carry no problem_id / provider / model.
    body = writer.path.read_text()
    assert "wrong_service_selector" not in body
    assert "claude" not in body
    assert "sonnet" not in body
    # Neither does the digest nor the full read-back.
    digest = store.digest(writer.path)
    assert digest is not None
    assert "wrong_service_selector" not in digest.text
    assert "wrong_service_selector" not in store.read_full(writer.path)
    # The sidecar index DOES retain it (analysis only), outside the app dir.
    idx = (tmp_path / ".index" / "hotelreservation.index").read_text()
    assert "wrong_service_selector_hotel_reservation" in idx
    assert "claude" in idx
    # The sidecar is not discoverable as a trajectory.
    found = store.digests("hotelReservation")
    assert len(found) == 1
    assert all(".index" not in d.path for d in found)


def test_store_layout_is_per_app_and_accumulates(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    for i in range(3):
        w = store.open_run(_meta(problem=f"p{i}", ts=f"2026060{i}_000000"))
        w.close(outcome="completed")
    digests = store.digests("hotelReservation")
    assert len(digests) == 3
    # All under the app-slug subdir.
    assert all("hotelreservation" in d.path for d in digests)
    # Scoping to an unknown app yields nothing.
    assert store.digests("socialNetwork") == []


def test_read_full_renders_transcript(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    w = store.open_run(_meta())
    rec = TrajectoryRecorder(w)
    rec.on_thinking("investigating")
    rec.on_tool_call("bash", "kubectl get pods")
    rec.on_tool_result("bash", stdout="profile CrashLoopBackOff")
    w.close(outcome="root cause: missing DB_HOST")

    text = store.read_full(w.path)
    assert "investigating" in text
    assert "kubectl get pods" in text
    assert "profile CrashLoopBackOff" in text
    assert "missing DB_HOST" in text


def test_in_flight_run_is_not_discovered_until_close(tmp_path: Path) -> None:
    """An open (uncommitted) run is a ``.partial`` file, hidden from retrieval."""
    store = TrajectoryStore(tmp_path)
    w = store.open_run(_meta())
    TrajectoryRecorder(w).on_thinking("still investigating")

    # Mid-run: nothing is discoverable, and the final .jsonl does not exist yet.
    assert store.digests("hotelReservation") == []
    assert not w.path.exists()
    assert w.path.with_suffix(".jsonl.partial").exists()

    w.close(outcome="completed")
    # After close: atomically committed and now discoverable.
    assert w.path.exists()
    assert not w.path.with_suffix(".jsonl.partial").exists()
    assert len(store.digests("hotelReservation")) == 1


def test_app_dir_points_at_per_app_subdir(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    assert store.app_dir("hotelReservation") == tmp_path / "hotelreservation"


def test_reader_skips_unparseable_lines(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    w = store.open_run(_meta())
    w.close(outcome="completed")
    # Inject a junk line into the committed file; the reader must skip it.
    with w.path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    digest = store.digest(w.path)
    assert digest is not None
    assert digest.outcome == "completed"


def test_recorder_never_raises_into_agent(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    w = store.open_run(_meta())
    w.close(outcome="completed")
    rec = TrajectoryRecorder(w)
    # Writing after close is a no-op, not an error.
    rec.on_thinking("late event")
    rec.on_tool_call("bash", None)
