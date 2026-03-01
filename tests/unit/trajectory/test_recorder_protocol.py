"""Tests for TrajectoryRecorderProtocol consistency and partial implementations."""

from pathlib import Path

from app_operator.trajectory import (
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
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

            def add_user_message(self, content):
                pass

            def add_assistant_message(self, content, duration=None):
                pass

            def add_tool_call(self, tool, args, stdout="", stderr="",
                              exit_code=None, duration=None):
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


class TestRecorderGuardConsistency:
    """Verify that recorder method calls in agents use consistent hasattr guards."""

    def test_subagent_record_token_usage_guarded(self):
        """SubagentCodingAgent guards record_token_usage with hasattr."""
        import inspect
        from libs.agent_cli.subagent_agent import SubagentCodingAgent

        source = inspect.getsource(SubagentCodingAgent.generate)
        assert 'hasattr(self.recorder, "record_token_usage")' in source

    def test_subagent_add_assistant_message_guarded(self):
        """SubagentCodingAgent guards add_assistant_message with hasattr."""
        import inspect
        from libs.agent_cli.subagent_agent import SubagentCodingAgent

        source = inspect.getsource(SubagentCodingAgent._generate_fix)
        assert 'hasattr(self.recorder, "add_assistant_message")' in source

    def test_hybrid_record_token_usage_guarded(self):
        """HybridCodingAgent guards record_token_usage with hasattr."""
        import inspect
        from app_operator.cli_agent.hybrid_agent import HybridCodingAgent

        source = inspect.getsource(HybridCodingAgent.generate)
        assert 'hasattr(self.recorder, "record_token_usage")' in source

    def test_hybrid_add_assistant_message_guarded(self):
        """HybridCodingAgent guards add_assistant_message with hasattr."""
        import inspect
        from app_operator.cli_agent.hybrid_agent import HybridCodingAgent

        source = inspect.getsource(HybridCodingAgent._generate_fix)
        assert 'hasattr(self.recorder, "add_assistant_message")' in source

    def test_partial_recorder_does_not_crash_subagent(self, tmp_path):
        """A recorder missing record_token_usage should not crash SubagentCodingAgent."""
        import unittest.mock as mock
        from libs.agent_cli.subagent_agent import SubagentCodingAgent

        class MinimalRecorder:
            """Recorder that lacks record_token_usage and add_assistant_message."""
            pass

        agent = SubagentCodingAgent(model="test-model", recorder=MinimalRecorder())

        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        resp.choices[0].message.content = "summary"
        usage = mock.MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15
        resp.usage = usage

        with mock.patch("litellm.completion", return_value=resp):
            result = agent.generate(
                "Provide fix_summary for the deployment.",
                cwd=str(tmp_path),
            )

        assert result == "summary"

    def test_partial_recorder_does_not_crash_hybrid(self, tmp_path):
        """A recorder missing record_token_usage should not crash HybridCodingAgent."""
        import unittest.mock as mock
        from app_operator.cli_agent.hybrid_agent import HybridCodingAgent

        class MinimalRecorder:
            """Recorder that lacks record_token_usage and add_assistant_message."""
            pass

        agent = HybridCodingAgent(model="test-model", recorder=MinimalRecorder())

        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        resp.choices[0].message.content = "summary"
        usage = mock.MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15
        resp.usage = usage

        with mock.patch("litellm.completion", return_value=resp):
            result = agent.generate(
                "Provide fix_summary for the deployment.",
                cwd=str(tmp_path),
            )

        assert result == "summary"
