from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class GeminiEvent(ABC):
    """Base class for Gemini stream events."""

    @abstractmethod
    def render(self, log_prefix: str) -> Optional[str]:
        """Render the event as a string for terminal output."""
        pass

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Optional["GeminiEvent"]:
        """Factory method to create events from JSON data."""
        msg_type = data.get("type")

        if msg_type == "message":
            return MessageEvent(
                role=data.get("role", ""), content=data.get("content", "")
            )
        elif msg_type == "tool_use":
            return ToolUseEvent(
                tool_name=data.get("tool_name", "Tool"),
                tool_id=data.get("tool_id"),
                parameters=data.get("parameters"),
            )
        elif msg_type == "tool_result":
            return ToolResultEvent(
                output=data.get("output", ""), tool_id=data.get("tool_id")
            )
        return None


class MessageEvent(GeminiEvent):
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def render(self, log_prefix: str) -> Optional[str]:
        # Message rendering is handled specially due to streaming
        # This is just a placeholder or could handle non-streaming blocks
        return self.content


class ToolUseEvent(GeminiEvent):
    def __init__(self, tool_name: str, tool_id: Optional[str], parameters: Any):
        self.tool_name = tool_name
        self.tool_id = tool_id
        self.parameters = parameters

    def render(self, log_prefix: str) -> str:
        truncated_params = self._truncate_params(self.parameters)
        return f"{log_prefix} \033[34m[Tool Use] {self.tool_name} {truncated_params}\033[0m"

    def _truncate_params(self, params: Any) -> str:
        s = str(params)
        max_len = 200
        if len(s) > max_len:
            return s[:max_len] + "..."
        return s


class ToolResultEvent(GeminiEvent):
    def __init__(self, output: str, tool_id: Optional[str]):
        self.output = output
        self.tool_id = tool_id
        self.tool_name_resolved: str = "Tool"  # To be set externally

    def render(self, log_prefix: str) -> str:
        if not self.output:
            return f"{log_prefix} \033[32m{self.tool_name_resolved} ran successfully\033[0m"
        else:
            truncated = self._truncate(self.output)
            return f"{log_prefix} \033[32m[Tool Result] {truncated}\033[0m"

    def _truncate(self, content: str) -> str:
        lines = content.splitlines()
        max_lines = 10
        if len(lines) > max_lines * 2:
            return "\n".join(
                lines[:max_lines] + ["... (truncated) ..."] + lines[-max_lines:]
            )
        return content
