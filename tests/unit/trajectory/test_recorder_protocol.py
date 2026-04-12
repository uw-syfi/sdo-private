"""Tests for TrajectoryRecorderProtocol consistency and partial implementations."""

import unittest.mock as mock
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

            def record_prompt_artifact(self, artifact):
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

        from app_operator.cli_agent.subagent_agent import SubagentCodingAgent

        source = inspect.getsource(SubagentCodingAgent.generate)
        assert 'hasattr(self.recorder, "record_token_usage")' in source

    def test_subagent_add_assistant_message_guarded(self):
        """SubagentCodingAgent guards add_assistant_message with hasattr."""
        import inspect

        from app_operator.cli_agent.subagent_agent import SubagentCodingAgent

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
        from app_operator.cli_agent.subagent_agent import SubagentCodingAgent

        class MinimalRecorder:
            """Recorder that lacks record_token_usage and add_assistant_message."""

        agent = SubagentCodingAgent(model="test-model", recorder=MinimalRecorder())  # type: ignore[arg-type]

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
        from app_operator.cli_agent.hybrid_agent import HybridCodingAgent

        class MinimalRecorder:
            """Recorder that lacks record_token_usage and add_assistant_message."""

        agent = HybridCodingAgent(model="test-model", recorder=MinimalRecorder())  # type: ignore[arg-type]

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


class TestLiteLLMClient:
    """Verify LiteLLMClient token tracking and recorder integration."""

    def _make_response(self, content="ok", prompt=10, completion=5, total=15):
        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        resp.choices[0].message.content = content
        usage = mock.MagicMock()
        usage.prompt_tokens = prompt
        usage.completion_tokens = completion
        usage.total_tokens = total
        resp.usage = usage
        return resp

    def test_records_token_usage_after_complete(self):
        """complete() calls recorder.record_token_usage with running total."""
        from libs.agent_cli.llm_client import LiteLLMClient

        recorder = mock.MagicMock()
        client = LiteLLMClient("test-model", recorder=recorder)

        with mock.patch("litellm.completion", return_value=self._make_response()):
            client.complete([{"role": "user", "content": "hi"}])

        recorder.record_token_usage.assert_called_once_with(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )

    def test_accumulates_tokens_across_calls(self):
        """Token counts accumulate across multiple complete() calls."""
        from libs.agent_cli.llm_client import LiteLLMClient

        recorder = mock.MagicMock()
        client = LiteLLMClient("test-model", recorder=recorder)

        with mock.patch("litellm.completion", return_value=self._make_response()):
            client.complete([{"role": "user", "content": "first"}])
            client.complete([{"role": "user", "content": "second"}])

        assert client._token_usage == {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
        }
        # recorder called after each call with running total
        assert recorder.record_token_usage.call_count == 2
        recorder.record_token_usage.assert_called_with(
            {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        )

    def test_no_recorder_does_not_crash(self):
        """complete() works fine with recorder=None."""
        from libs.agent_cli.llm_client import LiteLLMClient

        client = LiteLLMClient("test-model")

        with mock.patch("litellm.completion", return_value=self._make_response("result")):
            result = client.complete([{"role": "user", "content": "hi"}])

        assert result == "result"

    def test_partial_recorder_missing_record_token_usage(self):
        """A recorder without record_token_usage is silently skipped."""
        from libs.agent_cli.llm_client import LiteLLMClient

        class MinimalRecorder:
            pass

        client = LiteLLMClient("test-model", recorder=MinimalRecorder())  # type: ignore[arg-type]

        with mock.patch("litellm.completion", return_value=self._make_response()):
            result = client.complete([{"role": "user", "content": "hi"}])

        assert result == "ok"
        assert client._token_usage["total_tokens"] == 15

    def test_rlm_coding_agent_file_gen_records_tokens(self, tmp_path):
        """RLMCodingAgent file-gen path records tokens via LiteLLMClient."""
        from app_operator.cli_agent.rlm_agent import RLMCodingAgent

        recorder = mock.MagicMock()
        agent = RLMCodingAgent(model="test-model", recorder=recorder)

        with mock.patch("litellm.completion", return_value=self._make_response("content")):
            agent.generate(
                "Write .sds/deploy.sh for the deployment.",
                cwd=str(tmp_path),
            )

        recorder.record_token_usage.assert_called()
        call_args = recorder.record_token_usage.call_args[0][0]
        assert call_args["total_tokens"] == 15

    def test_compact_history_tokens_tracked(self):
        """_compact_history() tokens are tracked via _llm_client, not lost."""
        from app_operator.cli_agent.rlm.recursive_agent import RecursiveDeploymentAgent

        recorder = mock.MagicMock()
        agent = RecursiveDeploymentAgent(
            trajectory=recorder,
            llm_provider="test-model",
            compaction=True,
            model_context_tokens=10,  # tiny threshold so compaction triggers
        )
        agent._messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]

        compact_resp = self._make_response("summary", prompt=20, completion=8, total=28)
        with mock.patch("litellm.completion", return_value=compact_resp):
            agent._compact_history()

        assert agent._llm_client._token_usage["prompt_tokens"] == 20
        recorder.record_token_usage.assert_called_with(
            {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
        )
