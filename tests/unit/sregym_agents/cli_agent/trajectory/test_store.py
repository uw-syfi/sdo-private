"""Unit tests for the JSONL trajectory store + recorder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sregym_agents.cli_agent.trajectory.store import (
    TrajectoryFindings,
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


def test_findings_drive_structured_digest(tmp_path: Path) -> None:
    """When the agent records findings, the digest text is built from those
    symptom-register fields (not the raw tool mechanics)."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    rec = TrajectoryRecorder(writer)
    rec.on_thinking("looking at pods")
    rec.on_tool_call("bash", {"command": "kubectl get endpoints"})
    writer.findings(
        TrajectoryFindings(
            situation="frontend returns 503; the profile service has no ready endpoints",
            tell="the profile Service selector did not match the deployment pod labels",
            root_cause="wrong service selector on the profile Service",
            fix="corrected the profile Service selector to match the deployment labels",
            affected_resource="service/profile",
        )
    )
    writer.close(outcome="completed")

    digest = store.digest(writer.path)
    assert digest is not None
    assert "situation: frontend returns 503" in digest.text
    assert "no ready endpoints" in digest.text
    assert "root_cause: wrong service selector" in digest.text
    assert "selector did not match" in digest.text
    # The structured digest supersedes the mechanics fallback.
    assert "tools:" not in digest.text
    # Still leak-safe: no problem_id reaches the digest or the file.
    assert "wrong_service_selector_demo" not in digest.text
    assert "wrong_service_selector_demo" not in writer.path.read_text()


def test_digest_falls_back_to_mechanics_without_findings(tmp_path: Path) -> None:
    """A run with no recorded findings keeps the tool-mechanics digest."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    rec = TrajectoryRecorder(writer)
    rec.on_thinking("looking at pods")
    rec.on_tool_call("bash", {"command": "kubectl get pods"})
    writer.close(outcome="completed")

    digest = store.digest(writer.path)
    assert digest is not None
    assert "tools: bash" in digest.text
    assert "looking at pods" in digest.text


def test_findings_last_write_wins(tmp_path: Path) -> None:
    """A revised record_findings call overwrites the earlier one for retrieval."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    writer.findings(TrajectoryFindings(root_cause="tempting wrong first guess"))
    writer.findings(TrajectoryFindings(root_cause="the confirmed root cause"))
    writer.close(outcome="completed")

    digest = store.digest(writer.path)
    assert digest is not None
    assert "the confirmed root cause" in digest.text
    assert "tempting wrong first guess" not in digest.text


def test_empty_findings_keep_mechanics_digest(tmp_path: Path) -> None:
    """A findings record with no content does not blank out the digest."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    TrajectoryRecorder(writer).on_tool_call("bash", "kubectl get pods")
    writer.findings(TrajectoryFindings())  # agent called the tool with nothing
    writer.close(outcome="completed")

    digest = store.digest(writer.path)
    assert digest is not None
    assert "tools: bash" in digest.text


def test_findings_appear_in_read_full(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    TrajectoryRecorder(writer).on_thinking("investigating")
    writer.findings(TrajectoryFindings(root_cause="missing DB_HOST env", fix="set DB_HOST"))
    writer.close(outcome="completed")

    text = store.read_full(writer.path)
    assert "investigating" in text
    assert "root_cause: missing DB_HOST env" in text
    assert "fix: set DB_HOST" in text


def test_findings_after_close_is_a_noop(tmp_path: Path) -> None:
    """A late record_findings call (after the writer committed) is dropped, not raised."""
    store = TrajectoryStore(tmp_path)
    writer = store.open_run(_meta())
    writer.close(outcome="completed")
    writer.findings(TrajectoryFindings(root_cause="too late"))  # no-op
    digest = store.digest(writer.path)
    assert digest is not None
    assert "too late" not in digest.text


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
