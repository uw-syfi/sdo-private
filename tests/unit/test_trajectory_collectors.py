"""Tests for trajectory collector implementations."""

import json
import time
from unittest.mock import patch

import pytest

from app_operator.trajectory_collectors import (
    collect_gemini_sessions,
)


@pytest.fixture
def mock_gemini_home(tmp_path, monkeypatch):
    """Create a mock Gemini session directory."""
    mock_home = tmp_path / "mock_gemini_home"
    mock_sessions = mock_home / ".gemini" / "tmp"
    monkeypatch.setattr(
        "app_operator.trajectory_collectors.GEMINI_SESSION_DIR", mock_sessions
    )
    return mock_sessions


def test_collect_gemini_sessions_no_sessions_dir(tmp_path):
    """Test collection when Gemini sessions directory doesn't exist."""
    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    with patch(
        "app_operator.trajectory_collectors.GEMINI_SESSION_DIR",
        tmp_path / "nonexistent",
    ):
        sessions = collect_gemini_sessions(
            sds_dir, trajectories_dir, run_timestamp, start_time_str
        )

    assert sessions == []


def test_collect_gemini_sessions_empty_dir(mock_gemini_home, tmp_path):
    """Test collection when sessions directory is empty."""
    mock_gemini_home.mkdir(parents=True, exist_ok=True)

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    assert sessions == []


def test_collect_gemini_sessions_success(mock_gemini_home, tmp_path):
    """Test successful collection of recent Gemini sessions."""
    # Create project directory and session files
    project_dir = mock_gemini_home / "test-project"
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)

    # Create session files
    session1 = chats_dir / "session-001.json"
    session2 = chats_dir / "session-002.json"

    session_data = {"id": "test", "messages": []}
    session1.write_text(json.dumps(session_data))
    session2.write_text(json.dumps(session_data))

    # Set modification times to be after our run start
    run_start = time.strptime("2024-01-01 12:00:00", "%Y-%m-%d %H:%M:%S")
    run_start_ts = time.mktime(run_start)

    # Make sessions recent (after run start)
    import os

    os.utime(session1, (run_start_ts + 10, run_start_ts + 10))
    os.utime(session2, (run_start_ts + 20, run_start_ts + 20))

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    assert len(sessions) == 2
    # Verify files were copied
    copied_dir = trajectories_dir / "gemini_sessions" / run_timestamp
    assert copied_dir.exists()
    assert (copied_dir / "test-project_session-001.json").exists()
    assert (copied_dir / "test-project_session-002.json").exists()


def test_collect_gemini_sessions_filters_old_sessions(mock_gemini_home, tmp_path):
    """Test that old sessions before run start are not collected."""
    project_dir = mock_gemini_home / "test-project"
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)

    # Create an old session file
    old_session = chats_dir / "session-old.json"
    old_session.write_text(json.dumps({"id": "old", "messages": []}))

    # Set modification time to be before our run start
    run_start = time.strptime("2024-01-01 12:00:00", "%Y-%m-%d %H:%M:%S")
    run_start_ts = time.mktime(run_start)

    import os

    os.utime(old_session, (run_start_ts - 100, run_start_ts - 100))

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    # Old session should not be collected
    assert len(sessions) == 0


def test_collect_gemini_sessions_skips_bin_directory(mock_gemini_home, tmp_path):
    """Test that bin directory is skipped during collection."""
    # Create a bin directory that should be skipped
    bin_dir = mock_gemini_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    (bin_dir / "some-file.json").write_text("test")

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    # bin directory should be ignored
    assert len(sessions) == 0


def test_collect_gemini_sessions_handles_multiple_projects(mock_gemini_home, tmp_path):
    """Test collection from multiple project directories."""
    run_start = time.strptime("2024-01-01 12:00:00", "%Y-%m-%d %H:%M:%S")
    run_start_ts = time.mktime(run_start)

    # Create multiple project directories
    for project_name in ["project1", "project2"]:
        project_dir = mock_gemini_home / project_name
        chats_dir = project_dir / "chats"
        chats_dir.mkdir(parents=True, exist_ok=True)

        session = chats_dir / "session-001.json"
        session.write_text(json.dumps({"id": project_name}))

        import os

        os.utime(session, (run_start_ts + 10, run_start_ts + 10))

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    assert len(sessions) == 2


def test_collect_gemini_sessions_exception_handling(mock_gemini_home, tmp_path):
    """Test that exceptions are caught and handled gracefully.

    When an invalid time format is provided, the function should catch the
    exception and return an empty list instead of crashing.
    """
    mock_gemini_home.mkdir(parents=True, exist_ok=True)

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    # Invalid time format to trigger exception
    start_time_str = "invalid-time-format"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    # Should return empty list on error, not crash
    assert sessions == []


def test_collect_gemini_sessions_returns_relative_paths(mock_gemini_home, tmp_path):
    """Test that returned paths are relative to sds_dir."""
    project_dir = mock_gemini_home / "test-project"
    chats_dir = project_dir / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)

    session = chats_dir / "session-001.json"
    session.write_text(json.dumps({"id": "test"}))

    run_start = time.strptime("2024-01-01 12:00:00", "%Y-%m-%d %H:%M:%S")
    run_start_ts = time.mktime(run_start)

    import os

    os.utime(session, (run_start_ts + 10, run_start_ts + 10))

    sds_dir = tmp_path / ".sds"
    trajectories_dir = sds_dir / "trajectories"
    run_timestamp = "20240101_120000"
    start_time_str = "2024-01-01 12:00:00"

    sessions = collect_gemini_sessions(
        sds_dir, trajectories_dir, run_timestamp, start_time_str
    )

    # Paths should be relative to sds_dir
    assert len(sessions) == 1
    assert sessions[0].startswith("trajectories/gemini_sessions/")
    assert not sessions[0].startswith("/")
