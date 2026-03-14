"""Tests for OperatorAgent middleware chain."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app_operator.pydantic_ai._base_agent import AgentMiddleware, OperatorAgent

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def echo(ctx, message: str) -> str:
    return f"echo: {message}"


def shout(ctx, text: str) -> str:
    return text.upper()


def _make_recorder():
    recorder = MagicMock()
    recorder.record_run = MagicMock()
    return recorder


def _make_deps():
    return None


def _make_agent(
    middleware=None,
    tools=None,
    call_tools="all",
    output_type=str,
):
    deps = _make_deps()
    recorder = _make_recorder()

    class ConcreteAgent(OperatorAgent):
        phase = "test_phase"
        agent_name = "Test Agent"

        def __init__(self):
            super().__init__(deps, recorder, middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=call_tools),
                deps_type=type(deps),
                output_type=output_type,
                tools=tools if tools is not None else [echo],
            )

    return ConcreteAgent()


class RecordingMiddleware(AgentMiddleware):
    """Middleware that records all before/after calls for inspection."""

    def __init__(self, name="mw"):
        self.name = name
        self.before_calls: list[tuple[str, dict]] = []
        self.after_calls: list[tuple[str, dict, Any]] = []

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        self.before_calls.append((tool_name, args))
        return True

    def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        self.after_calls.append((tool_name, args, result))


class RejectingMiddleware(AgentMiddleware):
    """Middleware that always rejects tool calls."""

    def __init__(self):
        self.before_called = False
        self.after_called = False

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        self.before_called = True
        return False

    def after_tool_call(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        self.after_called = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_before_called_with_correct_name_and_args():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("test")
    assert len(mw.before_calls) == 1
    name, args = mw.before_calls[0]
    assert name == "echo"
    assert "message" in args


def test_after_called_with_correct_name_args_and_result():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw])
    agent._run("test")
    assert len(mw.after_calls) == 1
    name, args, result = mw.after_calls[0]
    assert name == "echo"
    assert "message" in args
    assert "echo:" in result


def test_rejection_prevents_tool_execution():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    real_echo_called = []

    def tracked_echo(ctx, message: str) -> str:
        real_echo_called.append(message)
        return f"echo: {message}"

    mw = RejectingMiddleware()
    agent = _make_agent(middleware=[mw], tools=[tracked_echo])
    # Rejection raises ModelRetry; agent exhausts retries → UnexpectedModelBehavior
    with pytest.raises(UnexpectedModelBehavior):
        agent._run("test")
    assert real_echo_called == [], "Tool body should not have been called on rejection"


def test_after_not_called_on_rejection():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    mw = RejectingMiddleware()
    agent = _make_agent(middleware=[mw])
    with pytest.raises(UnexpectedModelBehavior):
        agent._run("test")
    assert not mw.after_called


def test_before_called_in_order():
    order: list[str] = []

    class OrderedMiddleware(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        def before_tool_call(self, tool_name, args):
            order.append(self.label)
            return True

    mw0 = OrderedMiddleware("mw0")
    mw1 = OrderedMiddleware("mw1")
    agent = _make_agent(middleware=[mw0, mw1])
    agent._run("test")
    assert order == ["mw0", "mw1"]


def test_after_called_in_reverse_order():
    order: list[str] = []

    class OrderedMiddleware(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        def after_tool_call(self, tool_name, args, result):
            order.append(self.label)

    mw0 = OrderedMiddleware("mw0")
    mw1 = OrderedMiddleware("mw1")
    agent = _make_agent(middleware=[mw0, mw1])
    agent._run("test")
    assert order == ["mw1", "mw0"]


def test_first_middleware_rejection_skips_second_before():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    mw1_called = []

    class SecondMiddleware(AgentMiddleware):
        def before_tool_call(self, tool_name, args):
            mw1_called.append(True)
            return True

    mw0 = RejectingMiddleware()
    mw1 = SecondMiddleware()
    agent = _make_agent(middleware=[mw0, mw1])
    with pytest.raises(UnexpectedModelBehavior):
        agent._run("test")
    assert mw1_called == [], "Second middleware before_tool_call should not be called after rejection"


def test_multiple_tools_all_intercepted():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], tools=[echo, shout])
    agent._run("test")
    intercepted_names = [name for name, _ in mw.before_calls]
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
    # First run invokes the tool via the middleware
    assert len(mw.before_calls) == 1
    history = result1.all_messages()
    # Second run with message history should complete without error
    result2 = agent._run("second", message_history=history)
    assert result2 is not None


def test_structured_output_unaffected():
    class MyOutput(BaseModel):
        value: str

    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], output_type=MyOutput)
    result = agent._run("test")
    assert result.output is not None


def test_before_not_called_when_no_tools_invoked():
    mw = RecordingMiddleware()
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("test")
    assert mw.before_calls == []


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
        agent._run("test")
    # after_tool_call must have been called with result=None (the exception path)
    assert len(after_calls) >= 1
    tool_name, args, result = after_calls[0]
    assert tool_name == "failing_tool"
    assert result is None


def test_trajectory_recorded_after_run():
    """TrajectoryMiddleware.after_run calls recorder.record_run with correct args."""
    deps = _make_deps()
    recorder = _make_recorder()

    class ConcreteAgent(OperatorAgent):
        phase = "p"
        agent_name = "A"

        def __init__(self):
            super().__init__(deps, recorder)
            self._agent = Agent(TestModel(call_tools=[]), deps_type=type(deps), output_type=str)

    agent = ConcreteAgent()
    agent._run("prompt")
    recorder.record_run.assert_called_once()
    call_args = recorder.record_run.call_args
    assert call_args[0][0] == "p"
    assert call_args[0][1] == "A"
