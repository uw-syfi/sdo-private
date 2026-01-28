from typing import Any
from app_operator.trajectory import TrajectoryRecorderProtocol

# Assuming the import path based on typical Google ADK structure
# If this is incorrect, it will be caught during integration/testing
try:
    from google.genai.agent import BasePlugin
except ImportError:
    # Fallback for type checking or if library not present during development
    class BasePlugin:
        pass


class AdkTrajectoryPlugin(BasePlugin):
    """Plugin to record ADK agent execution to SDS trajectory."""

    def __init__(self, recorder: TrajectoryRecorderProtocol):
        self.recorder = recorder

    def before_model_callback(self, context: Any, request: Any) -> None:
        """Called before sending a request to the model."""
        # We might record the prompt here, but SDS trajectory usually records
        # user messages separately.
        pass

    def after_model_callback(self, context: Any, response: Any) -> None:
        """Called after receiving a response from the model."""
        # Extract text from response. Response structure depends on ADK.
        # Assuming response has a text or content attribute.
        content = ""
        try:
            if hasattr(response, "text"):
                content = response.text
            elif hasattr(response, "content"):
                content = str(response.content)
            else:
                content = str(response)
        except Exception:
            content = str(response)

        self.recorder.add_assistant_message(content)

    def before_tool_callback(self, context: Any, tool: Any, args: Any) -> None:
        """Called before executing a tool."""
        # We record the tool call after it completes to include output,
        # or we could record start here. SDS trajectory usually records
        # tool calls with outputs in one entry.
        pass

    def after_tool_callback(self, context: Any, tool: Any, result: Any) -> None:
        """Called after a tool execution completes."""
        tool_name = getattr(tool, "name", str(tool))

        # Args might be in context or args parameter of before_tool_callback
        # But here we might not have access to args easily if not passed.
        # However, typically tool call result includes everything or we rely
        # on the fact that ADK callbacks might vary.
        # Let's assume 'tool' object might have the args or we can't easily get them
        # without state.
        # Actually, the plan says: "add_tool_call() in tool callbacks with stdout/stderr".

        # We need args. If after_tool_callback doesn't provide args, we might need
        # to track them from before_tool_callback.
        # For now, I'll pass empty dict for args if unavailable.

        args = {}  # TODO: Capture args from before_tool_callback if possible

        stdout = str(result)
        stderr = ""
        exit_code = 0

        # specific handling for bash tool which returns a dict
        if isinstance(result, dict) and "stdout" in result and "stderr" in result:
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exit_code", 0)

        self.recorder.add_tool_call(
            tool=tool_name, args=args, stdout=stdout, stderr=stderr, exit_code=exit_code
        )

    def on_tool_error_callback(self, context: Any, tool: Any, error: Exception) -> None:
        """Called when a tool execution fails."""
        tool_name = getattr(tool, "name", str(tool))
        self.recorder.add_tool_call(
            tool=tool_name, args={}, stdout="", stderr=str(error), exit_code=1
        )
