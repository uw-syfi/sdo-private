"""Tests for Gemini session collection in trajectory recording.

These tests verify that the TrajectoryRecorder works correctly with the Gemini
session collection feature through the public API. Detailed integration testing
of session collection is done in integration tests.
"""

import json
import pytest

from app_operator.trajectory import TrajectoryRecorder, Phase


@pytest.fixture
def recorder(tmp_path):
    """Create a TrajectoryRecorder instance."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    recorder = TrajectoryRecorder(repo_path)
    return recorder


def test_finalize_works_without_gemini_sessions(recorder):
    """Test that finalize() works when no Gemini sessions exist."""
    # Create a phase without corresponding Gemini sessions
    with recorder.phase(Phase.DEPLOYMENT):
        recorder.add_user_message("Deploy")
        recorder.add_assistant_message("Deploying...")

    trajectory_path = recorder.finalize()

    with open(trajectory_path) as f:
        data = json.load(f)

    # Should have empty gemini_sessions list
    assert data["gemini_sessions"] == []
    assert "calls" in data
    assert len(data["calls"]) == 1


def test_save_includes_gemini_sessions_field(recorder):
    """Test that save() includes the gemini_sessions field."""
    with recorder.phase(Phase.MONITORING):
        recorder.add_user_message("Monitor")
        recorder.add_assistant_message("Monitoring...")

    # Call save() instead of finalize()
    trajectory_path = recorder.save()

    with open(trajectory_path) as f:
        data = json.load(f)

    # Should have gemini_sessions field (even if empty)
    assert "gemini_sessions" in data


def test_multiple_phases_recorded_correctly(recorder):
    """Test that multiple phases are recorded with correct call IDs."""
    # Create two phases with different calls
    with recorder.phase(Phase.DEPLOYMENT):
        recorder.add_user_message("Task 1")
        recorder.add_assistant_message("Working...")
    call_id_1 = recorder.get_current_call_id()

    with recorder.phase(Phase.MONITORING):
        recorder.add_user_message("Task 2")
        recorder.add_assistant_message("Working...")
    call_id_2 = recorder.get_current_call_id()

    trajectory_path = recorder.finalize()

    with open(trajectory_path) as f:
        data = json.load(f)

    # Should have two calls recorded
    assert len(data["calls"]) == 2
    # Call IDs should be different
    assert call_id_1 != call_id_2
    # Call IDs should be sequential
    assert call_id_2 == call_id_1 + 1


def test_get_run_id_returns_consistent_value(recorder):
    """Test that get_run_id() returns a consistent value."""
    run_id_1 = recorder.get_run_id()
    run_id_2 = recorder.get_run_id()

    # Should return same run ID
    assert run_id_1 == run_id_2
    # Should be a non-empty string
    assert isinstance(run_id_1, str)
    assert len(run_id_1) > 0


def test_get_current_call_id_in_phase(recorder):
    """Test that get_current_call_id() returns valid ID inside a phase."""
    with recorder.phase(Phase.DEPLOYMENT):
        recorder.add_user_message("Deploy")
        call_id = recorder.get_current_call_id()

        # Should return a positive integer
        assert isinstance(call_id, int)
        assert call_id > 0


def test_finalize_creates_trajectory_file(recorder):
    """Test that finalize() creates a trajectory file."""
    with recorder.phase(Phase.DEPLOYMENT):
        recorder.add_user_message("Deploy")
        recorder.add_assistant_message("Deploying...")

    trajectory_path = recorder.finalize()

    # File should exist
    assert trajectory_path.exists()
    # Should be a JSON file
    assert trajectory_path.suffix == ".json"
    # Should contain valid JSON
    with open(trajectory_path) as f:
        data = json.load(f)
        assert isinstance(data, dict)


def test_trajectory_contains_metadata(recorder):
    """Test that trajectory contains required metadata fields."""
    with recorder.phase(Phase.DEPLOYMENT):
        recorder.add_user_message("Deploy")
        recorder.add_assistant_message("Deploying...")

    trajectory_path = recorder.finalize()

    with open(trajectory_path) as f:
        data = json.load(f)

    # Check required fields
    assert "metadata" in data
    assert "calls" in data
    assert "gemini_sessions" in data

    # Should have phase data (as direct keys, not under "phases")
    assert "deployment" in data or "monitoring" in data or "exploration" in data

    # Metadata should have start_time
    assert "start_time" in data["metadata"]
