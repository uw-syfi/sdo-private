from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, cast

from .utils import truncate_content, truncate_params


class CodexEvent(ABC):
    """Base class for Codex (``codex exec --json``) stream events."""

    @abstractmethod
    def render(self, log_prefix: str) -> str | None:
        """Render the event as a string for terminal output."""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CodexEvent | None:
        """Factory method to create events from JSON data.

        Codex emits JSONL of the form ``{"type": "...", ...}``. The main
        shapes observed from ``codex exec --json`` are:

        - ``thread.started`` / ``turn.started``: lifecycle markers.
        - ``item.started`` / ``item.completed`` with an ``item`` payload
          whose ``type`` disambiguates content (``agent_message``,
          ``command_execution``, etc.).
        - ``turn.completed`` with a ``usage`` payload.
        - ``turn.failed`` / ``error`` with an error message.
        """
        event_type = data.get("type")

        if event_type in ("thread.started", "turn.started"):
            return LifecycleEvent(event_type)

        if event_type in ("item.started", "item.completed"):
            item: dict[str, Any] = cast("dict[str, Any]", data.get("item") or {})
            item_type = cast("str | None", item.get("type"))
            item_id = cast("str | None", item.get("id"))
            completed = event_type == "item.completed"

            if item_type == "agent_message":
                if not completed:
                    # Only the completed frame carries text; the started
                    # frame is a no-op for rendering and recording.
                    return None
                return TextEvent(text=cast("str", item.get("text", "")))

            if item_type == "command_execution":
                command = cast("str", item.get("command", ""))
                if completed:
                    return ToolResultEvent(
                        tool_id=item_id,
                        output=cast("str", item.get("aggregated_output", "")),
                        exit_code=cast("int | None", item.get("exit_code")),
                        status=cast("str | None", item.get("status")),
                    )
                return ToolUseEvent(
                    tool_id=item_id,
                    tool_name="shell",
                    parameters={"command": command},
                )

            if item_type in ("reasoning", "file_change", "mcp_tool_call", "web_search", "todo_list"):
                # Generic item types: surface started as a tool_use-style
                # marker and completed as a tool_result so downstream
                # consumers still see call/result pairs.
                if completed:
                    return ToolResultEvent(
                        tool_id=item_id,
                        output=_summarize_item(item),
                        exit_code=None,
                        status=cast("str | None", item.get("status")),
                    )
                return ToolUseEvent(
                    tool_id=item_id,
                    tool_name=item_type,
                    parameters=_item_parameters(item),
                )

            return None

        if event_type == "turn.completed":
            usage = cast("dict[str, Any]", data.get("usage") or {})
            return TurnCompletedEvent(
                input_tokens=int(usage.get("input_tokens") or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            )

        if event_type in ("turn.failed", "error"):
            message = ""
            if event_type == "turn.failed":
                err: Any = data.get("error") or {}
                if isinstance(err, dict):
                    err_dict = cast("dict[str, Any]", err)
                    message = cast("str", err_dict.get("message", ""))
                else:
                    message = str(err)
            else:
                message = cast("str", data.get("message", ""))
            return ErrorEvent(message=message)

        return None


def _item_parameters(item: dict[str, Any]) -> dict[str, Any]:
    """Extract a parameter dict from a generic codex item payload."""
    return {k: v for k, v in item.items() if k not in ("id", "type", "status")}


def _summarize_item(item: dict[str, Any]) -> str:
    """Summarize a generic codex item for the tool-result output field."""
    for key in ("text", "summary", "output", "result"):
        val = item.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


class LifecycleEvent(CodexEvent):
    """Session/turn lifecycle marker; not rendered."""

    def __init__(self, event_type: str):
        self.event_type = event_type

    def render(self, log_prefix: str) -> str | None:
        return None


class TextEvent(CodexEvent):
    """Assistant text content event (``agent_message`` item)."""

    def __init__(self, text: str):
        self.text = text

    def render(self, log_prefix: str) -> str | None:
        return self.text


class ToolUseEvent(CodexEvent):
    """Tool call start event (``item.started``)."""

    def __init__(self, tool_name: str, tool_id: str | None, parameters: Any):
        self.tool_name = tool_name
        self.tool_id = tool_id
        self.parameters = parameters

    def render(self, log_prefix: str) -> str:
        truncated = truncate_params(self.parameters)
        return f"{log_prefix} \033[34m[Tool Use] {self.tool_name} {truncated}\033[0m"


class ToolResultEvent(CodexEvent):
    """Tool call completion event (``item.completed``)."""

    def __init__(
        self,
        output: Any,
        tool_id: str | None,
        exit_code: int | None = None,
        status: str | None = None,
    ):
        if isinstance(output, list):
            output_list = cast("list[Any]", output)
            self.output = "\n".join(str(item) for item in output_list)
        else:
            self.output = str(output) if output else ""
        self.tool_id = tool_id
        self.exit_code = exit_code
        self.status = status
        self.tool_name_resolved: str = "Tool"  # Set by the session from tool_map

    def render(self, log_prefix: str) -> str:
        if not self.output:
            return f"{log_prefix} \033[32m{self.tool_name_resolved} ran successfully\033[0m"
        truncated = truncate_content(self.output)
        return f"{log_prefix} \033[32m[Tool Result] {truncated}\033[0m"


class TurnCompletedEvent(CodexEvent):
    """Final turn summary event.

    Carries per-turn usage. Codex already emits ``cached_input_tokens`` as
    a subset of ``input_tokens`` (OpenAI billing semantics), so no schema
    adapter is needed on the consumer side.
    """

    def __init__(
        self,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
    ):
        self.input_tokens = input_tokens
        self.cached_input_tokens = cached_input_tokens
        self.output_tokens = output_tokens

    def render(self, log_prefix: str) -> str | None:
        return None


class ErrorEvent(CodexEvent):
    """Error event emitted by codex on turn failure."""

    def __init__(self, message: str):
        self.message = message

    def render(self, log_prefix: str) -> str:
        return f"{log_prefix} \033[31m[Error] {self.message}\033[0m"
