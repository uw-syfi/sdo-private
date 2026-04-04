"""Unit tests for TurnLoggingMiddleware."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from libs.agent_mw import TurnLoggingMiddleware
from libs.pydantic_agent import BaseAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def echo(ctx, message: str) -> str:
    return f"echo: {message}"


def _make_agent(middleware=None, call_tools: Any = "all"):
    class ConcreteAgent(BaseAgent):
        def __init__(self):
            super().__init__(None, agent_name="test-agent", middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=call_tools),
                deps_type=type(None),
                output_type=str,
                tools=[echo],
            )

    return ConcreteAgent()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_on_function_tool_call_logs_info():
    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger)
    agent = _make_agent(middleware=[mw])
    agent._run("hi")
    assert mock_logger.info.called
    # Check the tool call log line was emitted
    call_args_list = [str(c) for c in mock_logger.info.call_args_list]
    assert any("echo" in s for s in call_args_list)


def test_after_run_logs_final_output():
    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger)
    agent = _make_agent(middleware=[mw], call_tools=[])
    agent._run("hello")
    # after_run should log the final output
    assert mock_logger.info.called
    last_call = mock_logger.info.call_args_list[-1]
    assert "test-agent" in str(last_call)


def test_on_function_tool_result_warns_on_retry_prompt():
    from pydantic_ai.messages import RetryPromptPart

    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger)

    # Attach middleware to a dummy agent
    class DummyAgent:
        agent_name = "dummy"
        current_request_input_tokens = 0

    mw.on_attach(DummyAgent())  # type: ignore[arg-type]

    retry_part = MagicMock(spec=RetryPromptPart)
    retry_part.tool_name = "some_tool"
    retry_part.model_response.return_value = "error message"

    event = MagicMock()
    event.result = retry_part

    mw.on_function_tool_result(event)
    assert mock_logger.warning.called


def test_on_function_tool_result_warns_on_failed_tool():
    from pydantic_ai.messages import ToolReturnPart

    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger)

    class DummyAgent:
        agent_name = "dummy"
        current_request_input_tokens = 0

    mw.on_attach(DummyAgent())  # type: ignore[arg-type]

    return_part = MagicMock(spec=ToolReturnPart)
    return_part.tool_name = "exec_bash"
    return_part.content = {"success": False, "exit_code": 1, "stderr": "some error"}

    event = MagicMock()
    event.result = return_part

    mw.on_function_tool_result(event)
    assert mock_logger.warning.called


def test_uses_default_logger_when_none_provided():
    mw = TurnLoggingMiddleware()
    assert mw._logger is not None
    assert isinstance(mw._logger, logging.Logger)


def test_context_window_none_shows_question_mark():
    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger, context_window=None)
    # Use call_tools="all" so on_function_tool_call fires and logs the usage prefix
    agent = _make_agent(middleware=[mw], call_tools="all")
    agent._run("hi")
    all_calls = " ".join(str(c) for c in mock_logger.info.call_args_list)
    assert "?k" in all_calls


def test_context_window_shown_in_prefix():
    mock_logger = MagicMock(spec=logging.Logger)
    mw = TurnLoggingMiddleware(logger=mock_logger, context_window=200_000)
    agent = _make_agent(middleware=[mw], call_tools="all")
    agent._run("hi")
    all_calls = " ".join(str(c) for c in mock_logger.info.call_args_list)
    assert "200k" in all_calls
