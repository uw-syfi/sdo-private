from contextlib import contextmanager
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, ToolMessage
from loguru import logger as loguru_logger

from app_operator.langgraph.utils import _DISPLAY_HEAD, _DISPLAY_TAIL, _truncate_for_display, invoke_agent


@contextmanager
def capture_logs():
    """Capture loguru log messages for the duration of the context."""
    messages = []
    sink_id = loguru_logger.add(lambda msg: messages.append(msg), format="{message}", colorize=False)
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


def test_invoke_agent_simple():
    state = {"agent_token_usage": []}
    mock_agent = MagicMock()

    # Mock stream output
    ai_msg = AIMessage(
        content="Hello world",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }
        },
    )
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    mock_recorder = MagicMock()

    result = invoke_agent(
        state,
        mock_agent,
        "System prompt",
        "User prompt",
        agent_name="Test Agent",
        recorder=mock_recorder,
    )

    assert result.text == "Hello world"
    assert len(result.messages) == 3  # System, User, AI
    assert result.structured is None
    assert state["agent_token_usage"] == [
        {"agent": "Test Agent", "input": 10, "output": 5, "total": 15},
    ]

    # Check recorder calls — system and user prompts recorded via record_message
    mock_recorder.add_system_message.assert_called_with("System prompt")
    mock_recorder.add_user_message.assert_called_with("User prompt")
    mock_recorder.add_assistant_message.assert_called()


def test_invoke_agent_with_tools():
    state = {}
    mock_agent = MagicMock()
    mock_ui = MagicMock()

    tool_call = {"name": "test_tool", "args": {"arg": "val"}, "id": "call_1"}
    ai_msg = AIMessage(content="", tool_calls=[tool_call])
    tool_msg = ToolMessage(content="Tool result", tool_call_id="call_1")

    mock_agent.stream.return_value = [
        {"node": {"messages": [ai_msg]}},
        {"tools": {"messages": [tool_msg]}},
    ]

    result = invoke_agent(state, mock_agent, "", "User prompt", ui=mock_ui)

    assert len(result.messages) == 3  # User, AI (tool call), Tool
    mock_ui.on_tool_call.assert_called_once_with("test_tool", "{'arg': 'val'}")


def test_invoke_agent_anthropic_usage():
    state = {"agent_token_usage": []}
    mock_agent = MagicMock()

    # Mock stream output for Anthropic style usage
    ai_msg = AIMessage(
        content="Hello",
        response_metadata={"usage": {"input_tokens": 20, "output_tokens": 10}},
    )
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    invoke_agent(state, mock_agent, "", "User prompt", agent_name="Anthropic Agent")

    assert state["agent_token_usage"] == [
        {"agent": "Anthropic Agent", "input": 20, "output": 10, "total": 30},
    ]


def test_invoke_agent_usage_metadata():
    """Token usage is extracted from usage_metadata (standardized LangChain attribute)."""
    state = {"agent_token_usage": []}
    mock_agent = MagicMock()

    ai_msg = AIMessage(
        content="Hello",
        usage_metadata={"input_tokens": 30, "output_tokens": 15, "total_tokens": 45},
    )
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    invoke_agent(state, mock_agent, "", "User prompt", agent_name="Gemini Agent")

    assert state["agent_token_usage"] == [
        {"agent": "Gemini Agent", "input": 30, "output": 15, "total": 45},
    ]


class TestTruncateForDisplay:
    def test_short_text_unchanged(self):
        text = "hello world"
        assert _truncate_for_display(text) == text

    def test_exactly_at_limit_unchanged(self):
        text = "a" * (_DISPLAY_HEAD + _DISPLAY_TAIL)
        assert _truncate_for_display(text) == text

    def test_long_text_shows_head_and_tail(self):
        head = "H" * _DISPLAY_HEAD
        middle = "M" * 200
        tail = "T" * _DISPLAY_TAIL
        text = head + middle + tail
        result = _truncate_for_display(text)
        assert result.startswith(head)
        assert result.endswith(tail)
        assert "200 chars omitted" in result

    def test_long_text_omitted_count_is_correct(self):
        total = _DISPLAY_HEAD + _DISPLAY_TAIL
        extra = 123
        text = "x" * (total + extra)
        result = _truncate_for_display(text)
        assert f"{extra} chars omitted" in result


def test_tool_call_args_logged_in_full():
    """Tool call args are never truncated in the log output."""
    state = {}
    mock_agent = MagicMock()

    long_args = "z" * (_DISPLAY_HEAD + _DISPLAY_TAIL + 500)
    tool_call = {"name": "bash", "args": {"command": long_args}, "id": "call_1"}
    ai_msg = AIMessage(content="", tool_calls=[tool_call])
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    with capture_logs() as messages:
        invoke_agent(state, mock_agent, "", "prompt")

    logged = "\n".join(messages)
    assert long_args in logged
    assert "omitted" not in logged


def test_tool_result_long_content_truncated_with_head_and_tail():
    """Long tool results are displayed with head + tail, not just head."""
    state = {}
    mock_agent = MagicMock()

    head = "HEAD" * 200   # 800 chars
    tail = "TAIL" * 200   # 800 chars
    middle = "MIDDLE" * 100
    long_content = head + middle + tail

    tool_call = {"name": "bash", "args": {}, "id": "call_2"}
    ai_msg = AIMessage(content="", tool_calls=[tool_call])
    tool_msg = ToolMessage(content=long_content, tool_call_id="call_2")
    mock_agent.stream.return_value = [
        {"node": {"messages": [ai_msg]}},
        {"tools": {"messages": [tool_msg]}},
    ]

    with capture_logs() as messages:
        invoke_agent(state, mock_agent, "", "prompt")

    logged = "\n".join(messages)
    assert "HEAD" in logged
    assert "TAIL" in logged
    assert "omitted" in logged


def test_invoke_agent_usage_metadata_preferred_over_response_metadata():
    """usage_metadata takes precedence over response_metadata."""
    state = {"agent_token_usage": []}
    mock_agent = MagicMock()

    ai_msg = AIMessage(
        content="Hello",
        usage_metadata={"input_tokens": 30, "output_tokens": 15, "total_tokens": 45},
        response_metadata={"token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    )
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    invoke_agent(state, mock_agent, "", "User prompt", agent_name="Test Agent")

    assert state["agent_token_usage"] == [
        {"agent": "Test Agent", "input": 30, "output": 15, "total": 45},
    ]
