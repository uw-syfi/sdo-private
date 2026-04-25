from __future__ import annotations

import importlib
from typing import Any

try:
    _agentshim_events = importlib.import_module("agentshim.events")
    _compose_event_handlers = getattr(_agentshim_events, "compose_event_handlers", None)
except ImportError:
    _compose_event_handlers = None


class _CompositeEventHandler:
    def __init__(self, handlers: list[Any]):
        self.handlers = list(handlers)

    def on_thinking(self, text: str) -> None:
        for handler in self.handlers:
            handler.on_thinking(text)

    def on_tool_call(self, tool: str, args: dict[str, Any] | str | None = None) -> None:
        for handler in self.handlers:
            handler.on_tool_call(tool, args)

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        for handler in self.handlers:
            handler.on_tool_result(tool, stdout, stderr, exit_code, duration)

    def on_usage(self, usage: dict[str, Any]) -> None:
        for handler in self.handlers:
            on_usage = getattr(handler, "on_usage", None)
            if on_usage is not None:
                on_usage(usage)


def compose_event_handlers(
    event_handler: Any | None = None,
    event_handlers: list[Any] | None = None,
) -> Any | None:
    if _compose_event_handlers is not None:
        return (_compose_event_handlers)(event_handler, event_handlers)  # type: ignore[operator]

    if event_handler is not None and event_handlers is not None:
        raise ValueError("Pass either event_handler or event_handlers, not both")
    if event_handlers is None:
        return event_handler
    handlers = list(event_handlers)
    if len(handlers) == 1:
        return handlers[0]
    return _CompositeEventHandler(handlers)
