import inspect
from typing import Any

import pytest

from agentshim import CodingAgent
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
