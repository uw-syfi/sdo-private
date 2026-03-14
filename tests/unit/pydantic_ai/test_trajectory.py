"""Tests for PydanticAITrajectoryRecorder."""

import json
from unittest.mock import MagicMock

import pytest
from pydantic_ai import RunUsage

from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


@pytest.fixture
def recorder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return PydanticAITrajectoryRecorder(repo)


def test_init_creates_trajectory_file(recorder):
    assert recorder.trajectory_file.exists()
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["status"] == "running"
    assert data["phases"] == []


def test_set_agent_name(recorder):
    recorder.set_agent_name("TestAgent")
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["agent_name"] == "TestAgent"


def test_record_run(recorder):
    # Mock a RunResult
    mock_result = MagicMock()
    mock_result.all_messages.return_value = []
    mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50, requests=1)

    # Patch ModelMessagesTypeAdapter
    with pytest.MonkeyPatch.context() as mp:
        import pydantic_ai.messages as pai_messages

        mock_adapter = MagicMock()
        mock_adapter.dump_python.return_value = [{"type": "test"}]
        mp.setattr(pai_messages, "ModelMessagesTypeAdapter", mock_adapter)

        recorder.record_run("exploration", "Code Analyzer", mock_result)

    data = json.loads(recorder.trajectory_file.read_text())
    assert len(data["phases"]) == 1
    phase = data["phases"][0]
    assert phase["phase"] == "exploration"
    assert phase["agent_name"] == "Code Analyzer"
    assert phase["call_id"] == 1
    assert phase["usage"]["input_tokens"] == 100
    assert phase["usage"]["output_tokens"] == 50


def test_record_run_increments_call_id(recorder):
    mock_result = MagicMock()
    mock_result.all_messages.return_value = []
    mock_result.usage.return_value = MagicMock(input_tokens=10, output_tokens=5, requests=1)

    with pytest.MonkeyPatch.context() as mp:
        import pydantic_ai.messages as pai_messages

        mock_adapter = MagicMock()
        mock_adapter.dump_python.return_value = []
        mp.setattr(pai_messages, "ModelMessagesTypeAdapter", mock_adapter)

        recorder.record_run("exploration", "Agent1", mock_result)
        recorder.record_run("deployment", "Agent2", mock_result)

    data = json.loads(recorder.trajectory_file.read_text())
    assert data["phases"][0]["call_id"] == 1
    assert data["phases"][1]["call_id"] == 2


def test_record_token_usage(recorder):
    recorder.record_token_usage(RunUsage(input_tokens=200, output_tokens=100, requests=3))
    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["token_usage"]["input_tokens"] == 200
    assert data["metadata"]["token_usage"]["output_tokens"] == 100
    assert data["metadata"]["token_usage"]["requests"] == 3


def test_finalize(recorder):
    result_path = recorder.finalize("completed")
    assert result_path == recorder.trajectory_file

    data = json.loads(recorder.trajectory_file.read_text())
    assert data["metadata"]["status"] == "completed"
    assert "end_time" in data["metadata"]


def test_finalize_creates_symlink(recorder):
    recorder.finalize("completed")
    link = recorder.repo_path / ".sds" / "trajectory.json"
    assert link.is_symlink() or link.exists()


def test_record_run_with_context(recorder):
    mock_result = MagicMock()
    mock_result.all_messages.return_value = []
    mock_result.usage.return_value = MagicMock(input_tokens=10, output_tokens=5, requests=1)

    with pytest.MonkeyPatch.context() as mp:
        import pydantic_ai.messages as pai_messages

        mock_adapter = MagicMock()
        mock_adapter.dump_python.return_value = []
        mp.setattr(pai_messages, "ModelMessagesTypeAdapter", mock_adapter)

        recorder.record_run("deployment", "Fixer", mock_result, context={"attempt": 2})

    data = json.loads(recorder.trajectory_file.read_text())
    assert data["phases"][0]["context"] == {"attempt": 2}
