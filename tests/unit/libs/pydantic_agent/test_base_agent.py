# pyright: reportPrivateUsage=false
"""Tests for the generic BaseAgent and AgentMiddleware in libs/pydantic_agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from libs.pydantic_agent import AgentMiddleware, BaseAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def echo(ctx: Any, message: str) -> str:
    return f"echo: {message}"


def shout(ctx: Any, text: str) -> str:
    return text.upper()


def _make_agent(
    middleware: list[AgentMiddleware] | None = None,
    tools: list[Any] | None = None,
    call_tools: Any = "all",
    output_type: Any = str,
    agent_name: str = "Test Agent",
) -> BaseAgent[Any]:
    class ConcreteAgent(BaseAgent[Any]):
        def __init__(self):
            super().__init__(None, agent_name=agent_name, middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=call_tools),  # type: ignore[arg-type]
                deps_type=type(None),
                output_type=output_type,
                tools=tools if tools is not None else [echo],
            )

    return ConcreteAgent()


class RecordingMiddleware(AgentMiddleware):
    def __init__(self, name: str = "mw"):
        self.name = name
        self.tool_call_events: list[Any] = []
        self.tool_result_events: list[Any] = []
        self.after_run_calls: list[tuple[Any, Any]] = []

    def on_function_tool_call(self, event: Any) -> None:
        self.tool_call_events.append(event)

    def on_function_tool_result(self, event: Any) -> None:
        self.tool_result_events.append(event)

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self.after_run_calls.append((result, run_ctx))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_on_function_tool_call_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("hi")
    assert len(mw.tool_call_events) == 1
    assert mw.tool_call_events[0].part.tool_name == "echo"


def test_on_function_tool_result_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("hi")
    assert len(mw.tool_result_events) == 1
    assert "echo:" in str(mw.tool_result_events[0].result.content)


def test_after_run_called_with_result_and_ctx():
    ctx = {"key": "value"}
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])  # type: ignore[arg-type]
    agent._run("hi", _run_ctx=ctx)
    assert len(mw.after_run_calls) == 1
    result, run_ctx = mw.after_run_calls[0]
    assert result is not None
    assert run_ctx == ctx


def test_after_run_called_with_none_ctx_by_default():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])  # type: ignore[arg-type]
    agent._run("hi")
    _result, run_ctx = mw.after_run_calls[0]
    assert run_ctx is None


def test_after_run_called_in_registration_order():
    order: list[str] = []

    class OrderedMw(AgentMiddleware):
        def __init__(self, label: str):
            self.label = label

        def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
            order.append(self.label)

    mw0 = OrderedMw("first")
    mw1 = OrderedMw("second")
    agent = _make_agent(middleware=[mw0, mw1], call_tools=[])  # type: ignore[arg-type]
    agent._run("hi")
    assert order == ["first", "second"]


def test_after_run_receives_result():
    received: list[Any] = []

    class CapturingMw(AgentMiddleware):
        def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
            received.append(result)

    agent = _make_agent(middleware=[CapturingMw()], call_tools=[])  # type: ignore[arg-type]
    result = agent._run("hi")
    assert len(received) == 1
    assert received[0] is result


def test_no_middleware_runs_cleanly():
    agent = _make_agent(middleware=[])
    result = agent._run("hi")
    assert result is not None


def test_on_attach_called_on_init():
    attached_agents: list[BaseAgent[Any]] = []

    class AttachRecordingMw(AgentMiddleware):
        def on_attach(self, agent: BaseAgent[Any]) -> None:
            super().on_attach(agent)
            attached_agents.append(agent)

    mw = AttachRecordingMw()
    agent = _make_agent(middleware=[mw])
    assert len(attached_agents) == 1
    assert attached_agents[0] is agent
    assert mw._agent is agent


def test_agent_name_property():
    agent = _make_agent(agent_name="My Agent")
    assert agent.agent_name == "My Agent"


def test_agent_name_accessible_from_middleware():
    names_seen: list[str] = []

    class NameCaptureMw(AgentMiddleware):
        def on_function_tool_call(self, event: Any) -> None:
            names_seen.append(self._agent.agent_name)

    agent = _make_agent(middleware=[NameCaptureMw()], agent_name="Captured Agent")
    agent._run("hi")
    assert names_seen == ["Captured Agent"]


def test_stream_events_called_in_registration_order():
    order: list[str] = []

    class OrderedMw(AgentMiddleware):
        def __init__(self, label: str):
            self.label = label

        def on_function_tool_call(self, event: Any) -> None:
            order.append(self.label)

    mw0 = OrderedMw("mw0")
    mw1 = OrderedMw("mw1")
    agent = _make_agent(middleware=[mw0, mw1])
    agent._run("hi")
    assert order == ["mw0", "mw1"]


def test_multiple_tools_all_produce_events():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], tools=[echo, shout])
    agent._run("test")
    intercepted_names = [e.part.tool_name for e in mw.tool_call_events]
    assert "echo" in intercepted_names
    assert "shout" in intercepted_names


def test_no_tool_events_when_no_tools_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])  # type: ignore[arg-type]
    agent._run("test")
    assert mw.tool_call_events == []


def test_hooks_work_with_message_history_continuation():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    result1 = agent._run("first")
    assert len(mw.tool_call_events) == 1
    history = result1.all_messages()
    result2 = agent._run("second", message_history=history)
    assert result2 is not None


def test_context_window_token_usage_tracks_run_usage():
    agent = _make_agent(call_tools=[])  # type: ignore[arg-type]
    agent._run("hi")
    assert agent.context_window_token_usage == (agent.current_run_usage.input_tokens or 0)


def test_structured_output_unaffected():
    class MyOutput(BaseModel):
        value: str

    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], output_type=MyOutput)  # type: ignore[arg-type]
    result = agent._run("test")
    assert result.output is not None
