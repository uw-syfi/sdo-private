import time
from typing import Dict, Any

from langchain_core.messages import AIMessage, ToolMessage, BaseMessage
from tools.trajectory import (
    record_user_message,
    record_assistant_message,
    record_tool_call,
)


def _extract_text(content: Any) -> str:
    """Extract text from message content, handling both string and list formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif isinstance(part, str):
                text_parts.append(part)
        return "".join(text_parts)
    return str(content)


class LangGraphTrajectoryHandler:
    """Handler for recording LangGraph interactions to the trajectory."""

    def __init__(self):
        self._pending_tool_calls: Dict[str, Dict[str, Any]] = {}
        self._tool_start_times: Dict[str, float] = {}

    def on_user_message(self, content: str):
        """Record a user message."""
        record_user_message(content)

    def process_message(self, message: BaseMessage):
        """Process a message from the LangGraph stream."""

        if isinstance(message, AIMessage):
            # Record thought content if present
            content = _extract_text(message.content)
            if content:
                record_assistant_message(content)

            # Record pending tool calls
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    call_id = tool_call["id"]
                    self._pending_tool_calls[call_id] = {
                        "name": tool_call["name"],
                        "args": tool_call["args"],
                    }
                    self._tool_start_times[call_id] = time.time()

        elif isinstance(message, ToolMessage):
            call_id = message.tool_call_id
            if call_id in self._pending_tool_calls:
                tool_info = self._pending_tool_calls.pop(call_id)
                start_time = self._tool_start_times.pop(call_id, None)
                duration = time.time() - start_time if start_time else None

                content = _extract_text(message.content)
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

                record_tool_call(
                    tool=tool_info["name"],
                    args=tool_info["args"],
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration=duration,
                )
