"""Comprehensive tests for trajectory recording.

These tests cover edge cases beyond basic Gemini session tests:
- Concurrent phase tracking
- Message serialization with special characters
- Error handling during finalization
- Trajectory file corruption recovery
"""

import json
import pytest
import threading
import time

from app_operator.trajectory import (
    TrajectoryRecorder,
    Phase,
    TrajectoryMessage,
)


@pytest.fixture
def recorder(tmp_path):
    """Create a TrajectoryRecorder instance."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    return TrajectoryRecorder(repo_path)


class TestConcurrentPhaseTracking:
    """Test trajectory recording with concurrent operations."""

    def test_sequential_phases_have_unique_call_ids(self, recorder):
        """Test that sequential phases get unique, incrementing call IDs."""
        call_ids = []

        with recorder.phase(Phase.DEPLOYMENT):
            call_ids.append(recorder.get_current_call_id())
            recorder.add_user_message("Deploy task 1")

        with recorder.phase(Phase.MONITORING):
            call_ids.append(recorder.get_current_call_id())
            recorder.add_user_message("Monitor task 1")

        with recorder.phase(Phase.DEPLOYMENT):
            call_ids.append(recorder.get_current_call_id())
            recorder.add_user_message("Deploy task 2")

        # All call IDs should be unique
        assert len(call_ids) == len(set(call_ids))
        # Call IDs should be sequential
        assert call_ids == sorted(call_ids)
        # Call IDs should increment by 1
        assert call_ids[1] == call_ids[0] + 1
        assert call_ids[2] == call_ids[1] + 1

    def test_concurrent_message_additions(self, recorder):
        """Test adding messages from multiple threads doesn't corrupt data."""
        errors = []

        def add_messages(thread_id):
            try:
                with recorder.phase(Phase.DEPLOYMENT):
                    for i in range(10):
                        recorder.add_user_message(f"Thread {thread_id} message {i}")
                        recorder.add_assistant_message(f"Response {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_messages, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0

        # Save and verify trajectory is valid
        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Should have 5 calls (one per thread)
        assert len(data["calls"]) == 5

    def test_phase_context_manager_with_exception(self, recorder):
        """Test phase context manager properly handles exceptions."""
        with pytest.raises(ValueError):
            with recorder.phase(Phase.DEPLOYMENT):
                recorder.add_user_message("Start deployment")
                raise ValueError("Simulated error")

        # Phase should have been ended with "failed" status
        trajectory_path = recorder.save()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Deployment phase should have one conversation
        assert len(data["deployment"]) == 1
        # Last message should indicate failure
        messages = data["deployment"][0]["messages"]
        assert any("failed" in str(m).lower() for m in messages)


class TestMessageSerialization:
    """Test message serialization with special characters and edge cases."""

    def test_special_characters_in_messages(self, recorder):
        """Test messages with special characters are properly serialized."""
        special_chars = '\\n\\t"\'<>&\x00\u2603\U0001F4A9'

        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message(f"Message with special chars: {special_chars}")
            recorder.add_assistant_message(f"Response with chars: {special_chars}")

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Messages should be retrievable and match
        messages = data["deployment"][0]["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert special_chars in user_msg["content"]

    def test_unicode_in_tool_calls(self, recorder):
        """Test tool calls with Unicode characters in arguments and output."""
        unicode_args = {"path": "/path/with/中文/文件.txt", "content": "Привет мир 🌍"}
        unicode_stdout = "Output: 日本語テキスト\nLine 2: 한글"
        unicode_stderr = "Error: Ελληνικά"

        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_tool_call(
                tool="write_file",
                args=unicode_args,
                stdout=unicode_stdout,
                stderr=unicode_stderr,
                exit_code=0,
            )

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Tool call should be properly serialized
        messages = data["deployment"][0]["messages"]
        tool_msg = next(m for m in messages if m.get("tool") == "write_file")
        assert tool_msg["args"]["path"] == unicode_args["path"]
        assert unicode_stdout in tool_msg["stdout"]
        assert unicode_stderr in tool_msg["stderr"]

    def test_very_long_output_truncation(self, recorder):
        """Test that very long outputs are properly truncated."""
        # Create output longer than max_output_length
        long_stdout = "x" * 15000  # Exceeds default 10000 limit
        long_stderr = "y" * 15000

        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_tool_call(
                tool="run_command",
                args={"cmd": "test"},
                stdout=long_stdout,
                stderr=long_stderr,
                exit_code=0,
            )

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        messages = data["deployment"][0]["messages"]
        tool_msg = next(m for m in messages if m.get("tool") == "run_command")

        # Output should be truncated
        assert len(tool_msg["stdout"]) < len(long_stdout)
        assert len(tool_msg["stderr"]) < len(long_stderr)
        # Should indicate truncation
        assert "truncated" in tool_msg["stdout"]
        assert "truncated" in tool_msg["stderr"]

    def test_empty_messages_handled_correctly(self, recorder):
        """Test that empty messages are handled correctly."""
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("")
            recorder.add_assistant_message("")
            recorder.add_tool_call("cmd", {}, stdout="", stderr="", exit_code=0)

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Should have messages recorded
        assert len(data["deployment"]) > 0

    def test_newlines_in_messages(self, recorder):
        """Test multiline messages are preserved."""
        multiline_msg = "Line 1\nLine 2\nLine 3\n"

        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message(multiline_msg)
            recorder.add_assistant_message(multiline_msg)

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        messages = data["deployment"][0]["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert user_msg["content"] == multiline_msg


class TestErrorHandlingAndRecovery:
    """Test error handling during trajectory recording."""

    def test_finalize_is_idempotent(self, recorder):
        """Test that calling finalize multiple times is safe."""
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("Test")

        path1 = recorder.finalize()
        path2 = recorder.finalize()
        path3 = recorder.finalize()

        # All paths should be the same
        assert path1 == path2 == path3
        # File should exist
        assert path1.exists()

    def test_recording_after_finalize_handled(self, recorder):
        """Test that recording after finalize doesn't crash."""
        recorder.finalize()

        # These should not crash (though they may be no-ops)
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("After finalize")

        # Should still be able to save
        path = recorder.save()
        assert path.exists()

    def test_recording_without_phase_context(self, recorder):
        """Test recording messages without active phase is handled gracefully."""
        # These should not crash, but may log warnings
        recorder.add_user_message("No phase")
        recorder.add_assistant_message("No phase response")
        recorder.add_tool_call("cmd", {}, stdout="out", stderr="err")

        # Should still be able to finalize
        path = recorder.finalize()
        assert path.exists()

    def test_concurrent_finalization(self, recorder):
        """Test concurrent finalize calls don't cause issues."""
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("Test")

        paths = []
        errors = []

        def finalize_concurrent():
            try:
                path = recorder.finalize()
                paths.append(path)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=finalize_concurrent) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0
        # All paths should be the same
        assert len(set(str(p) for p in paths)) == 1


class TestTrajectoryStructure:
    """Test trajectory file structure and metadata."""

    def test_trajectory_contains_all_required_fields(self, recorder):
        """Test that trajectory file contains all required fields."""
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("Deploy")

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Required top-level fields
        assert "metadata" in data
        assert "calls" in data
        assert "deployment" in data
        assert "monitoring" in data
        assert "exploration" in data
        assert "script_generation" in data
        assert "gemini_sessions" in data

        # Required metadata fields
        assert "repo_path" in data["metadata"]
        assert "start_time" in data["metadata"]
        assert "end_time" in data["metadata"]
        assert "status" in data["metadata"]
        assert "run_id" in data["metadata"]

    def test_call_records_have_timing_info(self, recorder):
        """Test that call records include timing information."""
        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_user_message("Task")
            time.sleep(0.01)  # Small delay

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        # Should have one call
        assert len(data["calls"]) == 1
        call = data["calls"][0]

        # Should have timing info
        assert "call_id" in call
        assert "phase" in call
        assert "start_time" in call
        assert "end_time" in call
        # End time should be after start time
        assert call["end_time"] is not None

    def test_set_agent_name(self, recorder):
        """Test setting agent name in metadata."""
        recorder.set_agent_name("TestAgent-v1.0")

        trajectory_path = recorder.save()
        with open(trajectory_path) as f:
            data = json.load(f)

        assert data["metadata"]["agent_name"] == "TestAgent-v1.0"

    def test_custom_max_output_length(self, tmp_path):
        """Test custom max_output_length parameter."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        recorder = TrajectoryRecorder(repo_path, max_output_length=100)

        long_output = "x" * 200

        with recorder.phase(Phase.DEPLOYMENT):
            recorder.add_tool_call("cmd", {}, stdout=long_output, stderr="")

        trajectory_path = recorder.finalize()
        with open(trajectory_path) as f:
            data = json.load(f)

        messages = data["deployment"][0]["messages"]
        tool_msg = next(m for m in messages if m.get("tool") == "cmd")

        # Output should be truncated to roughly max_output_length
        assert len(tool_msg["stdout"]) < 200


class TestTrajectoryMessage:
    """Test TrajectoryMessage dataclass."""

    def test_to_dict_excludes_none_values(self):
        """Test that to_dict excludes None values."""
        msg = TrajectoryMessage(
            role="user",
            content="Hello",
            tool=None,
            args=None,
            stdout=None,
            stderr=None,
            exit_code=None,
        )

        d = msg.to_dict()

        # Should only have role and content
        assert "role" in d
        assert "content" in d
        assert "tool" not in d
        assert "args" not in d
        assert "stdout" not in d
        assert "stderr" not in d
        assert "exit_code" not in d

    def test_to_dict_includes_all_fields_when_present(self):
        """Test that to_dict includes all fields when they have values."""
        msg = TrajectoryMessage(
            role="tool_call",
            content=None,
            tool="run_command",
            args={"cmd": "test"},
            stdout="output",
            stderr="error",
            exit_code=1,
            timestamp="2024-01-01 12:00:00",
            duration_seconds=1.5,
        )

        d = msg.to_dict()

        assert d["role"] == "tool_call"
        assert "content" not in d
        assert d["tool"] == "run_command"
        assert d["args"] == {"cmd": "test"}
        assert d["stdout"] == "output"
        assert d["stderr"] == "error"
        assert d["exit_code"] == 1
        assert d["timestamp"] == "2024-01-01 12:00:00"
        assert d["duration_seconds"] == 1.5
