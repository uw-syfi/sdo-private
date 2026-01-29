import json
import time
import os
from pathlib import Path
from unittest.mock import patch
import pytest

from app_operator.trajectory import (
    TrajectoryRecorder,
)


# Mock the GEMINI_SESSION_DIR constant in trajectory.py
@pytest.fixture
def mock_gemini_dir(tmp_path):
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()

    with patch("app_operator.trajectory.GEMINI_SESSION_DIR", gemini_dir):
        yield gemini_dir


@pytest.fixture
def recorder(tmp_path):
    # Initialize recorder with a temporary repo path
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    recorder = TrajectoryRecorder(repo_path)
    return recorder


def create_gemini_session_structure(gemini_dir, run_id, call_id=None, time_offset=0):
    """Helper to create the Gemini session directory structure."""
    # Create project directory (hash-like name)
    project_dir = gemini_dir / "project_12345"
    project_dir.mkdir(exist_ok=True)

    chats_dir = project_dir / "chats"
    chats_dir.mkdir(exist_ok=True)

    current_time = time.time() + time_offset

    # Create metadata file if call_id is provided
    if call_id is not None:
        metadata = {
            "run_id": run_id,
            "call_id": call_id,
            "start_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(current_time - 5)
            ),
        }
        metadata_file = chats_dir / f"sds_call_{call_id:03d}.json"
        with open(metadata_file, "w") as f:
            json.dump(metadata, f)

        # Set mtime
        os.utime(metadata_file, (current_time - 5, current_time - 5))

    # Create session file
    session_data = {"messages": [{"role": "user", "content": "hello"}]}
    session_file = chats_dir / "session-123.json"
    with open(session_file, "w") as f:
        json.dump(session_data, f)

    # Set mtime slightly after metadata
    os.utime(session_file, (current_time, current_time))

    return session_file


def test_collect_gemini_sessions_with_metadata_match(recorder, mock_gemini_dir):
    """Test collecting sessions where metadata file provides the match."""
    # Setup call record in recorder
    call_id = 1
    start_time = time.time()
    recorder.trajectory["calls"].append(
        {
            "call_id": call_id,
            "phase": "exploration",
            "start_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(start_time)
            ),
            "end_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(start_time + 60)
            ),
        }
    )

    # Create gemini session with metadata
    create_gemini_session_structure(
        mock_gemini_dir, recorder._run_timestamp, call_id=call_id, time_offset=10
    )

    # Run collection
    recorder._collect_gemini_sessions()

    # Verify results
    assert "gemini_sessions" in recorder.trajectory
    sessions = recorder.trajectory["gemini_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["call_id"] == call_id

    # Verify file copied and renamed
    dest_file = (
        recorder.trajectories_dir
        / "gemini_sessions"
        / recorder._run_timestamp
        / f"gemini_session_call_{call_id:03d}.json"
    )
    assert dest_file.exists()


def test_collect_gemini_sessions_timing_match(recorder, mock_gemini_dir):
    """Test collecting sessions matched by timing (no metadata file)."""
    # Setup call record
    call_id = 2
    start_time = time.time()
    recorder.trajectory["calls"].append(
        {
            "call_id": call_id,
            "phase": "exploration",
            "start_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(start_time)
            ),
            "end_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(start_time + 60)
            ),
        }
    )

    # Create session file without metadata, inside the time window
    create_gemini_session_structure(
        mock_gemini_dir, "other_run", call_id=None, time_offset=10
    )

    recorder._collect_gemini_sessions()

    sessions = recorder.trajectory["gemini_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["call_id"] == call_id

    dest_file = (
        recorder.trajectories_dir
        / "gemini_sessions"
        / recorder._run_timestamp
        / f"gemini_session_call_{call_id:03d}.json"
    )
    assert dest_file.exists()


def test_collect_gemini_sessions_no_match(recorder, mock_gemini_dir):
    """Test collecting sessions that don't match any call."""
    # Session time well after any call
    create_gemini_session_structure(
        mock_gemini_dir, recorder._run_timestamp, call_id=None, time_offset=1000
    )

    recorder._collect_gemini_sessions()

    sessions = recorder.trajectory["gemini_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["call_id"] is None

    # Should get sequential numbering
    dest_file = (
        recorder.trajectories_dir
        / "gemini_sessions"
        / recorder._run_timestamp
        / "gemini_session_001.json"
    )
    assert dest_file.exists()


def test_collect_gemini_sessions_ignore_old_files(recorder, mock_gemini_dir):
    """Test that files from before the run are ignored."""
    # Session time before run start
    # Reset run start time to now
    now = time.time()
    recorder.trajectory["metadata"]["start_time"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(now)
    )

    create_gemini_session_structure(
        mock_gemini_dir, recorder._run_timestamp, call_id=None, time_offset=-100
    )

    recorder._collect_gemini_sessions()

    assert recorder.trajectory["gemini_sessions"] == []


def test_collect_gemini_sessions_error_handling(recorder, mock_gemini_dir):
    """Test error handling during collection."""
    # Create a condition that causes error (e.g. permission error on directory iteration)
    # We can mock GEMINI_SESSION_DIR.iterdir to raise exception

    with patch("pathlib.Path.iterdir", side_effect=OSError("Permission denied")):
        # This shouldn't crash
        recorder._collect_gemini_sessions()


def test_write_to_file_error_handling(recorder):
    """Test error handling when writing trajectory file fails."""
    # Mock open to raise exception
    with patch("builtins.open", side_effect=OSError("Disk full")):
        # Should catch exception and log warning
        recorder._write_to_file()


def test_update_latest_link_symlink_error(recorder):
    """Test fallback when symlink creation fails."""
    # Create the trajectory file first
    with open(recorder.trajectory_file, "w") as f:
        f.write("{}")

    # Mock symlink_to to raise OSError
    with patch.object(Path, "symlink_to", side_effect=OSError("Symlink failed")):
        # Should try to copy instead
        recorder._update_latest_link()

        # Verify copy exists
        assert recorder._latest_link.exists()
        assert not recorder._latest_link.is_symlink()


def test_update_latest_link_copy_error(recorder):
    """Test when both symlink and copy fail."""
    with open(recorder.trajectory_file, "w") as f:
        f.write("{}")

    with (
        patch.object(Path, "symlink_to", side_effect=OSError("Symlink failed")),
        patch("shutil.copy2", side_effect=OSError("Copy failed")),
    ):
        # Should fail silently
        recorder._update_latest_link()


def test_match_session_to_call_edge_cases(recorder):
    """Test edge cases for session matching."""
    # Setup call with no end time (ongoing)
    call_id = 3
    start_time = time.time()
    recorder.trajectory["calls"].append(
        {
            "call_id": call_id,
            "phase": "monitoring",
            "start_time": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(start_time)
            ),
            "end_time": None,
        }
    )

    # Session modified after start
    match = recorder._match_session_to_call(start_time + 10)
    assert match == call_id

    # Session modified before start
    match = recorder._match_session_to_call(start_time - 10)
    assert match is None
