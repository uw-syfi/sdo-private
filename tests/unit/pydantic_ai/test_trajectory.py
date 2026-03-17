"""Tests for PydanticAITrajectoryRecorder and RecorderPathProvider."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic_ai import RunUsage

from app_operator.pydantic_ai._trajectory import (
    PydanticAITrajectoryRecorder,
    RecorderPathProvider,
    _sanitize_for_filename,
)


@pytest.fixture
def recorder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return PydanticAITrajectoryRecorder(repo)


# ---------------------------------------------------------------------------
# _sanitize_for_filename
# ---------------------------------------------------------------------------


def test_sanitize_basic():
    assert _sanitize_for_filename("Health Agent") == "health_agent"


def test_sanitize_special_chars():
    assert _sanitize_for_filename("My-Agent (v2)") == "my_agent_v2"


def test_sanitize_already_clean():
    assert _sanitize_for_filename("deploy") == "deploy"


# ---------------------------------------------------------------------------
# PydanticAITrajectoryRecorder init
# ---------------------------------------------------------------------------


def test_init_creates_session_dir(recorder):
    assert recorder._session_dir.exists()
    assert recorder._session_dir.is_dir()


def test_init_creates_metadata_file(recorder):
    assert recorder.trajectory_file.exists()
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["status"] == "running"
    assert data["phases"] == {}


def test_init_phases_is_dict(recorder):
    data = json.loads(recorder.trajectory_file.read_text())
    assert isinstance(data["phases"], dict)


# ---------------------------------------------------------------------------
# next_agent_path
# ---------------------------------------------------------------------------


def test_next_agent_path_returns_jsonl_under_session_dir(recorder):
    path = recorder.next_agent_path("deployment", "health_agent")
    assert path.parent == recorder._session_dir
    assert path.suffix == ".jsonl"


def test_next_agent_path_increments_counter(recorder):
    p1 = recorder.next_agent_path("deployment", "health_agent")
    p2 = recorder.next_agent_path("deployment", "repair_agent")
    assert p1.name.startswith("001_")
    assert p2.name.startswith("002_")


def test_next_agent_path_registers_in_metadata(recorder):
    recorder.next_agent_path("deployment", "health_agent")
    data = json.loads(recorder.trajectory_file.read_text())
    assert "deployment" in data["phases"]
    assert len(data["phases"]["deployment"]) == 1
    assert data["phases"]["deployment"][0].endswith(".jsonl")


def test_next_agent_path_multiple_phases(recorder):
    recorder.next_agent_path("code_analysis", "analyze_agent")
    recorder.next_agent_path("deployment", "health_agent")
    recorder.next_agent_path("deployment", "repair_agent")
    data = json.loads(recorder.trajectory_file.read_text())
    assert len(data["phases"]["code_analysis"]) == 1
    assert len(data["phases"]["deployment"]) == 2


def test_next_agent_path_writes_metadata_immediately(recorder):
    """metadata.json is written before the run completes (crash-resilient)."""
    recorder.next_agent_path("monitoring", "health_agent")
    data = json.loads(recorder.trajectory_file.read_text())
    assert len(data["phases"]["monitoring"]) == 1


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------


def test_record_usage_accumulates(recorder):
    recorder.record_usage(RunUsage(input_tokens=100, output_tokens=50, requests=2))
    recorder.record_usage(RunUsage(input_tokens=200, output_tokens=100, requests=3))
    assert recorder.total_usage.input_tokens == 300
    assert recorder.total_usage.output_tokens == 150
    assert recorder.total_usage.requests == 5


def test_record_usage_does_not_write_to_disk(recorder, tmp_path):
    """record_usage only accumulates in memory; no extra disk writes."""
    mtime_before = recorder.trajectory_file.stat().st_mtime
    recorder.record_usage(RunUsage(input_tokens=10, output_tokens=5, requests=1))
    mtime_after = recorder.trajectory_file.stat().st_mtime
    assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def test_finalize_returns_metadata_path(recorder):
    result = recorder.finalize("completed")
    assert result == recorder.trajectory_file


def test_finalize_sets_status(recorder):
    recorder.finalize("completed")
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["status"] == "completed"


def test_finalize_writes_end_time(recorder):
    recorder.finalize("completed")
    data = json.loads(recorder.trajectory_file.read_text())
    assert "end_time" in data["metadata"]


def test_finalize_writes_token_usage(recorder):
    recorder.record_usage(RunUsage(input_tokens=200, output_tokens=100, requests=3))
    recorder.finalize("completed")
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["token_usage"]["input_tokens"] == 200
    assert data["metadata"]["token_usage"]["output_tokens"] == 100
    assert data["metadata"]["token_usage"]["requests"] == 3


def test_finalize_creates_symlink(recorder):
    recorder.finalize("completed")
    link = recorder.repo_path / ".sds" / "trajectory.json"
    assert link.is_symlink() or link.exists()


def test_finalize_symlink_points_to_metadata(recorder):
    recorder.finalize("completed")
    link = recorder.repo_path / ".sds" / "trajectory.json"
    assert link.is_symlink()
    assert link.resolve() == recorder.trajectory_file.resolve()


# ---------------------------------------------------------------------------
# _write_metadata logs warning on OSError
# ---------------------------------------------------------------------------


def test_write_metadata_logs_warning_on_oserror(recorder):
    with patch.object(Path, "write_text", side_effect=OSError("permission denied")):
        with patch("app_operator.pydantic_ai._trajectory.logger") as mock_logger:
            recorder._write_metadata()
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# RecorderPathProvider
# ---------------------------------------------------------------------------


def test_recorder_path_provider_uses_run_ctx_phase(recorder):
    provider = RecorderPathProvider(recorder)
    path = provider.get_path("health_agent", {"phase": "deployment", "agent_name": "HealthAgent"})
    assert "deployment" in path.name
    assert path.suffix == ".jsonl"


def test_recorder_path_provider_uses_run_ctx_agent_name(recorder):
    provider = RecorderPathProvider(recorder)
    path = provider.get_path("fallback", {"phase": "monitoring", "agent_name": "My Monitor"})
    assert "my_monitor" in path.name


def test_recorder_path_provider_fallback_agent_name(recorder):
    """When run_ctx has no agent_name key, falls back to positional agent_name."""
    provider = RecorderPathProvider(recorder)
    path = provider.get_path("my_agent", {"phase": "deployment"})
    assert "my_agent" in path.name


def test_recorder_path_provider_no_run_ctx(recorder):
    provider = RecorderPathProvider(recorder)
    path = provider.get_path("my_agent", None)
    assert "unknown" in path.name
    assert path.suffix == ".jsonl"
