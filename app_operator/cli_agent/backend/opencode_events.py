from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class OpencodeEvent(ABC):
    """Base class for Opencode stream events."""

    @abstractmethod
    def render(self, log_prefix: str) -> Optional[str]:
        """Render the event as a string for terminal output."""
        pass

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Optional["OpencodeEvent"]:
        """Factory method to create events from JSON data."""
        msg_type = data.get("type")
        part = data.get("part", {})

        if msg_type == "text":
            return TextEvent(text=part.get("text", ""))
        elif msg_type == "tool_use":
            return ToolUseEvent(
                tool_name=part.get("tool", "Tool"),
                input_data=part.get("state", {}).get("input"),
                output_data=part.get("state", {}).get("output"),
                status=part.get("state", {}).get("status"),
            )
        elif msg_type == "step_start":
            return StepStartEvent()
        elif msg_type == "step_finish":
            return StepFinishEvent(
                reason=part.get("reason"),
                cost=part.get("cost"),
                tokens=part.get("tokens"),
            )

        return None


class TextEvent(OpencodeEvent):
    def __init__(self, text: str):
        self.text = text

    def render(self, log_prefix: str) -> Optional[str]:
        # We will handle text printing in the agent loop to handle potential streaming
        # or just print it as is.
        # For now, let's return it.
        return self.text


class ToolUseEvent(OpencodeEvent):
    def __init__(self, tool_name: str, input_data: Any, output_data: Any, status: str):
        self.tool_name = tool_name
        self.input_data = input_data
        self.output_data = output_data
        self.status = status

    def render(self, log_prefix: str) -> str:
        # Render tool use and result
        truncated_input = self._truncate(str(self.input_data))

        output_str = ""
        if self.output_data:
            truncated_output = self._truncate(str(self.output_data), max_lines=5)
            output_str = (
                f"\n{log_prefix} \033[32m[Tool Result] {truncated_output}\033[0m"
            )

        return f"{log_prefix} \033[34m[Tool Use] {self.tool_name} {truncated_input}\033[0m{output_str}"

    def _truncate(self, s: str, max_lines: int = 1) -> str:
        lines = s.splitlines()
        if len(lines) > max_lines:
            s = "\n".join(lines[:max_lines] + ["..."])

        if len(s) > 200 and max_lines == 1:
            return s[:200] + "..."
        return s


class StepStartEvent(OpencodeEvent):
    def render(self, log_prefix: str) -> Optional[str]:
        return None


class StepFinishEvent(OpencodeEvent):
    def __init__(
        self, reason: Optional[str], cost: Optional[float], tokens: Optional[Dict]
    ):
        self.reason = reason
        self.cost = cost
        self.tokens = tokens

    def render(self, log_prefix: str) -> Optional[str]:
        # Optional: Print cost info?
        # For now, maybe just ignore or print verbose.
        # Let's keep it clean.
        return None
