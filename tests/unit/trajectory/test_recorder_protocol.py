"""Tests for TrajectoryRecorderProtocol consistency and partial implementations."""

from pathlib import Path

from app_operator.trajectory import (
    NullTrajectoryRecorder,
    TrajectoryRecorderProtocol,
)


class TestTrajectoryRecorderProtocol:
    """Verify the protocol is runtime-checkable and NullTrajectoryRecorder satisfies it."""

    def test_null_recorder_is_instance_of_protocol(self):
        recorder = NullTrajectoryRecorder()
        assert isinstance(recorder, TrajectoryRecorderProtocol)

    def test_protocol_is_runtime_checkable(self):
        """A plain object should NOT satisfy the protocol."""

        class NotARecorder:
            pass

        assert not isinstance(NotARecorder(), TrajectoryRecorderProtocol)

    def test_partial_implementation_not_instance(self):
        """An object with only some methods should not satisfy the protocol."""

        class PartialRecorder:
            def add_user_message(self, content: str) -> None:
                pass

        assert not isinstance(PartialRecorder(), TrajectoryRecorderProtocol)

    def test_full_custom_implementation_is_instance(self):
        """A class implementing all required methods satisfies the protocol."""

        class CustomRecorder:
            def start_phase(self, phase, context=None):
                pass

            def end_phase(self, status=None):
                pass

            def add_system_message(self, content):
                pass

            def add_user_message(self, content):
                pass

            def add_assistant_message(self, content, duration=None):
                pass

            def add_tool_call(self, tool, args, stdout="", stderr="", exit_code=None, duration=None):
                pass

            def set_phase_status(self, status):
                pass

            def set_agent_name(self, agent_name):
                pass

            def set_prompt_version(self, version):
                pass

            def record_fallback(self):
                pass

            def record_prompt_kwargs(self, kwargs):
                pass

            def record_rendered_prompt(self, rendered_prompt):
                pass

            def record_fault_injection(self, metadata):
                pass

            def record_token_usage(self, usage):
                pass

            def finalize(self, status="completed"):
                return Path("/dev/null")

            def phase(self, phase, context=None):
                pass

        assert isinstance(CustomRecorder(), TrajectoryRecorderProtocol)
