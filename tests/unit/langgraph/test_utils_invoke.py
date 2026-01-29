from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, ToolMessage
from app_operator.langgraph.utils import invoke_agent


def test_invoke_agent_simple():
    state = {"token_usage": {"input": 0, "output": 0, "total": 0}}
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
        state, mock_agent, "System prompt", "User prompt", recorder=mock_recorder
    )

    assert text == "Hello world"
    assert len(messages) == 3  # System, User, AI
    assert state["token_usage"] == {"input": 10, "output": 5, "total": 15}

    # Check recorder calls
    mock_recorder.add_user_message.assert_called_with("User prompt")
    # LangGraphTrajectoryHandler processes messages.
    # For AIMessage, it calls add_assistant_message or add_tool_call
    mock_recorder.add_assistant_message.assert_called()


def test_invoke_agent_with_tools(capsys):
    state = {}
    mock_agent = MagicMock()

    tool_call = {"name": "test_tool", "args": {"arg": "val"}, "id": "call_1"}
    ai_msg = AIMessage(content="", tool_calls=[tool_call])
    tool_msg = ToolMessage(content="Tool result", tool_call_id="call_1")

    mock_agent.stream.return_value = [
        {"node": {"messages": [ai_msg]}},
        {"tools": {"messages": [tool_msg]}},
    ]

    text, messages = invoke_agent(state, mock_agent, "", "User prompt")

    assert len(messages) == 3  # User, AI (tool call), Tool

    captured = capsys.readouterr()
    assert "[Tool Use] test_tool {'arg': 'val'}" in captured.out
    assert "[Tool Result] Tool result" in captured.out


def test_invoke_agent_anthropic_usage():
    state = {"token_usage": {"input": 0, "output": 0, "total": 0}}
    mock_agent = MagicMock()

    # Mock stream output for Anthropic style usage
    ai_msg = AIMessage(
        content="Hello",
        response_metadata={"usage": {"input_tokens": 20, "output_tokens": 10}},
    )
    mock_agent.stream.return_value = [{"node": {"messages": [ai_msg]}}]

    invoke_agent(state, mock_agent, "", "User prompt")

    assert state["token_usage"] == {"input": 20, "output": 10, "total": 30}
