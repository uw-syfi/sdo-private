import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app_operator.langgraph.message_utils import extract_text
from app_operator.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol


class LangGraphTrajectoryHandler:
    """Handler for recording LangGraph interactions to the trajectory."""

    def __init__(self, recorder: TrajectoryRecorderProtocol | None = None):
        self.recorder = recorder or NullTrajectoryRecorder()
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._tool_start_times: dict[str, float] = {}

    def on_user_message(self, content: str):
        """Record a user message."""
        self.recorder.add_user_message(content)

    def process_message(self, message: BaseMessage):
        """Process a message from the LangGraph stream."""

        if isinstance(message, AIMessage):
            # Record thought content if present
            content = extract_text(message.content)
            if content:
                self.recorder.add_assistant_message(content)

            # Record pending tool calls
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    call_id = tool_call.get("id")
                    if call_id:
                        self._pending_tool_calls[call_id] = {
                            "name": tool_call["name"],
                            "args": tool_call["args"],
                        }
                        self._tool_start_times[call_id] = time.time()

        elif isinstance(message, ToolMessage):
            call_id = message.tool_call_id
            if call_id and call_id in self._pending_tool_calls:
                tool_info = self._pending_tool_calls.pop(call_id)
                start_time = self._tool_start_times.pop(call_id, None)
                duration = time.time() - start_time if start_time else None

                content = extract_text(message.content)
                stdout = content
                stderr = ""
                exit_code = None

                # Attempt to parse structured output for better logging if possible
                # e.g. if it's the bash tool, we might have formatted output
                if tool_info["name"] == "bash":
                    # Check if artifact exists and has structured data
                    # Note: LangGraph/LangChain might not populate artifact by default
                    # unless tool returns ToolMessage directly, which our tools.py doesn't do.
                    # But the string representation might be parseable or we just log it as is.
                    pass

                self.recorder.add_tool_call(
                    tool=tool_info["name"],
                    args=tool_info["args"],
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration=duration,
                )
