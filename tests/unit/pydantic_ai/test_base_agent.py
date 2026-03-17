"""Tests for OperatorAgent middleware chain."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app_operator.pydantic_ai._base_agent import OperatorAgent
from libs.pydantic_agent import AgentMiddleware

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def echo(ctx, message: str) -> str:
    return f"echo: {message}"


def shout(ctx, text: str) -> str:
    return text.upper()


def _make_recorder(tmp_path=None):
    recorder = MagicMock()
    recorder.next_agent_path = MagicMock(return_value=(tmp_path or Path("/tmp")) / "traj.jsonl")
    recorder.record_usage = MagicMock()
    return recorder


def _make_deps():
    deps = MagicMock()
    deps.config.agent.backend = "openai"
    deps.config.agent.model = "gpt-4o"
    deps.config.agent.step_limit = None
    return deps


def _make_agent(
    middleware=None,
    tools=None,
    call_tools="all",
    output_type=str,
):
    deps = _make_deps()
    recorder = _make_recorder()

    with patch("app_operator.pydantic_ai._base_agent.get_context_window", return_value=128_000):

        class ConcreteAgent(OperatorAgent):
            phase = "test_phase"

            def __init__(self):
                super().__init__(deps, recorder, agent_name="Test Agent", middleware=middleware or [])
                self._agent = Agent(
                    TestModel(call_tools=call_tools),
                    deps_type=type(deps),
                    output_type=output_type,
                    tools=tools if tools is not None else [echo],
                )

        return ConcreteAgent()


class RecordingMiddleware(AgentMiddleware):
    """Middleware that records stream events for inspection."""

    def __init__(self, name="mw"):
        self.name = name
        self.tool_call_events: list[Any] = []
        self.tool_result_events: list[Any] = []

    def on_function_tool_call(self, event: Any) -> None:
        self.tool_call_events.append(event)

    def on_function_tool_result(self, event: Any) -> None:
        self.tool_result_events.append(event)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_before_called_with_correct_name_and_args():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("test")
    assert len(mw.tool_call_events) == 1
    assert mw.tool_call_events[0].part.tool_name == "echo"
    assert "message" in (mw.tool_call_events[0].part.args or {})


def test_after_called_with_correct_name_and_result():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("test")
    assert len(mw.tool_result_events) == 1
    assert mw.tool_result_events[0].result.tool_name == "echo"
    assert "echo:" in str(mw.tool_result_events[0].result.content)


def test_stream_events_called_in_registration_order():
    order: list[str] = []

    class OrderedMiddleware(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        def on_function_tool_call(self, event):
            order.append(self.label)

    mw0 = OrderedMiddleware("mw0")
    mw1 = OrderedMiddleware("mw1")
    agent = _make_agent(middleware=[mw0, mw1])
    agent._run("test")
    assert order == ["mw0", "mw1"]


def test_multiple_tools_all_produce_events():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], tools=[echo, shout])
    agent._run("test")
    intercepted_names = [e.part.tool_name for e in mw.tool_call_events]
    assert "echo" in intercepted_names
    assert "shout" in intercepted_names


def test_no_middleware_runs_cleanly():
    agent = _make_agent(middleware=[])
    result = agent._run("test")
    assert result is not None


def test_hooks_work_with_message_history_continuation():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    result1 = agent._run("first")
    assert len(mw.tool_call_events) == 1
    history = result1.all_messages()
    result2 = agent._run("second", message_history=history)
    assert result2 is not None


def test_structured_output_unaffected():
    class MyOutput(BaseModel):
        value: str

    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], output_type=MyOutput)
    result = agent._run("test")
    assert result.output is not None


def test_no_events_when_no_tools_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("test")
    assert mw.tool_call_events == []


def test_trajectory_recorded_after_run(tmp_path):
    """TrajectoryMiddleware writes JSONL and recorder.record_usage is called after run."""
    deps = _make_deps()
    recorder = _make_recorder(tmp_path)

    with patch("app_operator.pydantic_ai._base_agent.get_context_window", return_value=128_000):

        class ConcreteAgent(OperatorAgent):
            phase = "p"

            def __init__(self):
                super().__init__(deps, recorder, agent_name="A")
                self._agent = Agent(TestModel(call_tools=[]), deps_type=type(deps), output_type=str)

        agent = ConcreteAgent()

    agent._run("prompt")
    recorder.next_agent_path.assert_called_once()
    recorder.record_usage.assert_called_once()
