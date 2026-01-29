from typing import Any

from app_operator.trajectory import TrajectoryRecorderProtocol
from google.adk.plugins import BasePlugin


class AdkTrajectoryPlugin(BasePlugin):
    """Plugin to record ADK agent execution to SDS trajectory."""

    def __init__(self, recorder: TrajectoryRecorderProtocol):
        self.recorder = recorder

    def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> None:
        """Called before sending a request to the model."""
        # We might record the prompt here, but SDS trajectory usually records
        # user messages separately.
        pass

    def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        """Called after receiving a response from the model."""
        # Extract text from response. Response structure depends on ADK.
        # Assuming response has a text or content attribute.
        content = ""
        try:
            if hasattr(llm_response, "text"):
                content = llm_response.text
            elif hasattr(llm_response, "content"):
                content = str(llm_response.content)
            else:
                content = str(llm_response)
        except Exception:
            content = str(llm_response)

        self.recorder.add_assistant_message(content)

    def before_tool_callback(
        self, *, tool: Any, tool_args: Any, tool_context: Any
    ) -> None:
        """Called before executing a tool."""
        # We record the tool call after it completes to include output,
        # or we could record start here. SDS trajectory usually records
        # tool calls with outputs in one entry.
        pass

    def after_tool_callback(
        self, *, tool: Any, tool_args: Any, tool_context: Any, result: Any
    ) -> None:
        """Called after a tool execution completes."""
        tool_name = getattr(tool, "name", str(tool))

        stdout = str(result)
        stderr = ""
        exit_code = 0

        # specific handling for bash tool which returns a dict
        if isinstance(result, dict) and "stdout" in result and "stderr" in result:
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exit_code", 0)

        self.recorder.add_tool_call(
            tool=tool_name,
            args=tool_args,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    def on_tool_error_callback(
        self, *, tool: Any, tool_args: Any, tool_context: Any, error: Exception
    ) -> None:
        """Called when a tool execution fails."""
        tool_name = getattr(tool, "name", str(tool))
        self.recorder.add_tool_call(
            tool=tool_name,
            args=tool_args,
            stdout="",
            stderr=str(error),
            exit_code=1,
        )
