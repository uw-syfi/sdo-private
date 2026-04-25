import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentshim import CodingAgent
from app_operator.cli_agent._event_handlers import TrajectoryAgentEventHandler, append_event_handler
from app_operator.cli_agent.hybrid_agent import HybridCodingAgent
from app_operator.cli_agent.rlm_official_agent import RLMOfficialAgent
from app_operator.cli_agent.subagent_agent import SubagentCodingAgent


class _RecordingHandler:
    def __init__(self):
        self.thinking: list[str] = []

    def on_thinking(self, text: str) -> None:
        self.thinking.append(text)

    def on_tool_call(self, tool, args=None) -> None:
        pass

    def on_tool_result(self, tool, stdout="", stderr="", exit_code=None, duration=None) -> None:
        pass

    def on_usage(self, usage) -> None:
        pass


@pytest.mark.parametrize("agent_cls", [SubagentCodingAgent, HybridCodingAgent, RLMOfficialAgent])
def test_custom_provider_constructors_accept_event_handlers(agent_cls):
    first = _RecordingHandler()
    second = _RecordingHandler()

    agent = agent_cls(model="test-model", event_handlers=[first, second])
    agent.event_handler.on_thinking("hello")

    assert first.thinking == ["hello"]
    assert second.thinking == ["hello"]


@pytest.mark.parametrize(
    ("provider", "agent_cls"),
    [
        ("subagent", SubagentCodingAgent),
        ("hybrid", HybridCodingAgent),
        ("rlm-official", RLMOfficialAgent),
    ],
)
def test_coding_agent_forwards_event_handlers_to_custom_providers(provider, agent_cls):
    if "event_handlers" not in inspect.signature(CodingAgent).parameters:
        pytest.skip("Pinned agentshim does not expose CodingAgent(event_handlers=...)")

    first = _RecordingHandler()
    second = _RecordingHandler()

    coding_agent_cls: Any = CodingAgent
    agent = coding_agent_cls(provider=provider, model="test-model", event_handlers=[first, second])

    assert isinstance(agent.backend, agent_cls)
    event_handler: Any = agent.backend.event_handler
    event_handler.on_thinking("hello")
    assert first.thinking == ["hello"]
    assert second.thinking == ["hello"]


def test_trajectory_event_handler_records_tool_results_with_pending_args():
    recorder = MagicMock()
    handler = TrajectoryAgentEventHandler(recorder)

    handler.on_tool_call("shell", {"command": "printf ok"})
    handler.on_tool_result("shell", stdout="ok", exit_code=0, duration=0.25)

    recorder.add_tool_call.assert_called_once_with(
        tool="shell",
        args={"command": "printf ok"},
        stdout="ok",
        stderr="",
        exit_code=0,
        duration=0.25,
    )


def test_trajectory_event_handler_records_normalized_usage():
    recorder = MagicMock()
    handler = TrajectoryAgentEventHandler(recorder)

    handler.on_usage(
        {
            "input_tokens": 100,
            "output_tokens": 25,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        }
    )

    recorder.record_token_usage.assert_called_once_with(
        {
            "prompt_tokens": 115,
            "completion_tokens": 25,
            "total_tokens": 140,
        }
    )


def test_trajectory_event_handler_records_cumulative_usage():
    recorder = MagicMock()
    handler = TrajectoryAgentEventHandler(recorder)

    handler.on_usage({"input_tokens": 10, "output_tokens": 2})
    handler.on_usage({"input_tokens": 20, "output_tokens": 3})

    recorder.record_token_usage.assert_called_with(
        {
            "prompt_tokens": 30,
            "completion_tokens": 5,
            "total_tokens": 35,
        }
    )


def test_append_event_handler_preserves_existing_handler():
    first = _RecordingHandler()
    second = _RecordingHandler()
    agent = SubagentCodingAgent(model="test-model", event_handler=first)

    append_event_handler(agent, second)
    assert agent.event_handler is not None
    agent.event_handler.on_thinking("hello")

    assert first.thinking == ["hello"]
    assert second.thinking == ["hello"]
