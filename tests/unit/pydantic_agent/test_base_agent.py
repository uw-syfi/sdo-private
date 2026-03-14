"""Tests for the generic BaseAgent and AgentMiddleware in libs/pydantic_agent."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from libs.pydantic_agent import AgentMiddleware, BaseAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def echo(ctx, message: str) -> str:
    return f"echo: {message}"


def _make_agent(
    middleware=None,
    tools=None,
    call_tools="all",
    output_type=str,
    agent_name="Test Agent",
):
    class ConcreteAgent(BaseAgent):
        def __init__(self):
            super().__init__(None, agent_name=agent_name, middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=call_tools),
                deps_type=type(None),
                output_type=output_type,
                tools=tools if tools is not None else [echo],
            )

    return ConcreteAgent()


class RecordingMiddleware(AgentMiddleware):
    def __init__(self, name="mw"):
        self.name = name
        self.before_calls: list[tuple[str, dict]] = []
        self.after_calls: list[tuple[str, dict, Any]] = []
        self.after_run_calls: list[tuple[Any, Any]] = []

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        self.before_calls.append((tool_name, args))
        return True

    def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        self.after_calls.append((tool_name, args, result))

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self.after_run_calls.append((result, run_ctx))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_before_tool_call_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("hi")
    assert len(mw.before_calls) == 1
    assert mw.before_calls[0][0] == "echo"


def test_after_tool_call_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("hi")
    assert len(mw.after_calls) == 1
    assert "echo:" in mw.after_calls[0][2]


def test_after_run_called_with_result_and_ctx():
    ctx = {"key": "value"}
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("hi", _run_ctx=ctx)
    assert len(mw.after_run_calls) == 1
    result, run_ctx = mw.after_run_calls[0]
    assert result is not None
    assert run_ctx == ctx


def test_after_run_called_with_none_ctx_by_default():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("hi")
    result, run_ctx = mw.after_run_calls[0]
    assert run_ctx is None


def test_after_run_called_in_registration_order():
    order: list[str] = []

    class OrderedMw(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        def after_run(self, result, run_ctx=None):
            order.append(self.label)

    mw0 = OrderedMw("first")
    mw1 = OrderedMw("second")
    agent = _make_agent(middleware=[mw0, mw1], call_tools=[])
    agent._run("hi")
    assert order == ["first", "second"]


def test_after_run_receives_result():
    received = []

    class CapturingMw(AgentMiddleware):
        def after_run(self, result, run_ctx=None):
            received.append(result)

    agent = _make_agent(middleware=[CapturingMw()], call_tools=[])
    result = agent._run("hi")
    assert len(received) == 1
    assert received[0] is result


def test_no_middleware_runs_cleanly():
    agent = _make_agent(middleware=[])
    result = agent._run("hi")
    assert result is not None


def test_after_run_not_called_on_tool_rejection():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    class RejectMw(AgentMiddleware):
        after_run_called = False

        def before_tool_call(self, tool_name, args):
            return False

        def after_run(self, result, run_ctx=None):
            self.after_run_called = True

    mw = RejectMw()
    agent = _make_agent(middleware=[mw])
    with pytest.raises(UnexpectedModelBehavior):
        agent._run("hi")
    assert not mw.after_run_called


def test_on_attach_called_on_init():
    attached_agents = []

    class AttachRecordingMw(AgentMiddleware):
        def on_attach(self, agent):
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
    names_seen = []

    class NameCaptureMw(AgentMiddleware):
        def before_tool_call(self, tool_name, args):
            names_seen.append(self._agent.agent_name)
            return True

    agent = _make_agent(middleware=[NameCaptureMw()], agent_name="Captured Agent")
    agent._run("hi")
    assert names_seen == ["Captured Agent"]


def test_after_tool_call_invoked_even_when_tool_raises():
    """after_tool_call must be called even if the wrapped tool raises an exception."""

    def failing_tool(ctx, message: str) -> str:
        raise RuntimeError("tool exploded")

    after_calls: list[tuple[str, dict, Any]] = []

    class TrackingMiddleware(AgentMiddleware):
        def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
            after_calls.append((tool_name, args, result))

    mw = TrackingMiddleware()
    agent = _make_agent(middleware=[mw], tools=[failing_tool])
    with pytest.raises(RuntimeError, match="tool exploded"):
        agent._run("hi")
    # after_tool_call must have been called with result=None (the exception path)
    assert len(after_calls) >= 1
    tool_name, args, result = after_calls[0]
    assert tool_name == "failing_tool"
    assert result is None
