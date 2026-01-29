from unittest.mock import MagicMock
from app_operator.adk.trajectory_plugin import AdkTrajectoryPlugin


def test_after_model_callback_records_message():
    recorder = MagicMock()
    plugin = AdkTrajectoryPlugin(recorder)

    response = MagicMock()
    response.text = "Hello world"

    plugin.after_model_callback(callback_context=None, llm_response=response)

    recorder.add_assistant_message.assert_called_once_with("Hello world")


def test_after_tool_callback_records_tool_call():
    recorder = MagicMock()
    plugin = AdkTrajectoryPlugin(recorder)

    tool = MagicMock()
    tool.name = "my_tool"

    # Test simple string result
    plugin.after_tool_callback(
        tool=tool, tool_args={"arg": "val"}, tool_context=None, result="Success"
    )

    recorder.add_tool_call.assert_called_with(
        tool="my_tool", args={"arg": "val"}, stdout="Success", stderr="", exit_code=0
    )


def test_after_tool_callback_records_bash_result():
    recorder = MagicMock()
    plugin = AdkTrajectoryPlugin(recorder)

    tool = MagicMock()
    tool.name = "bash"

    # Test dict result (like bash tool)
    result = {"stdout": "output", "stderr": "error", "exit_code": 1}

    plugin.after_tool_callback(
        tool=tool, tool_args="ls", tool_context=None, result=result
    )

    recorder.add_tool_call.assert_called_with(
        tool="bash", args="ls", stdout="output", stderr="error", exit_code=1
    )


def test_on_tool_error_callback_records_error():
    recorder = MagicMock()
    plugin = AdkTrajectoryPlugin(recorder)

    tool = MagicMock()
    tool.name = "broken_tool"

    error = ValueError("Something went wrong")

    plugin.on_tool_error_callback(
        tool=tool, tool_args={}, tool_context=None, error=error
    )

    recorder.add_tool_call.assert_called_with(
        tool="broken_tool",
        args={},
        stdout="",
        stderr="Something went wrong",
        exit_code=1,
    )
