from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, ToolMessage

from app_operator.langgraph.utils import invoke_agent


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

    text, messages = invoke_agent(
        state,
        mock_agent,
        "System prompt",
        "User prompt",
        agent_name="Test Agent",
        recorder=mock_recorder,
    )

    assert text == "Hello world"
    assert len(messages) == 3  # System, User, AI
    assert state["agent_token_usage"] == [
        {"agent": "Test Agent", "input": 10, "output": 5, "total": 15},
    ]

    # Check recorder calls
    mock_recorder.add_user_message.assert_called_with("User prompt")
    # LangGraphTrajectoryHandler processes messages.
    # For AIMessage, it calls add_assistant_message or add_tool_call
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

    text, messages = invoke_agent(state, mock_agent, "", "User prompt", ui=mock_ui)

    assert len(messages) == 3  # User, AI (tool call), Tool
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
