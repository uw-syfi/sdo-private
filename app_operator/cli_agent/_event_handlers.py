from __future__ import annotations

import importlib
from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from app_operator.trajectory import TokenUsage, TrajectoryRecorderProtocol

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


class TrajectoryAgentEventHandler:
    """Record generic coding-agent events into the SDS trajectory recorder."""

    def __init__(self, recorder: TrajectoryRecorderProtocol):
        self.recorder = recorder
        self._pending_tool_args: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_call(self, tool: str, args: dict[str, Any] | str | None = None) -> None:
        self._pending_tool_args[tool].append(self._normalize_args(args))

    def on_tool_result(
        self,
        tool: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> None:
        pending = self._pending_tool_args.get(tool)
        args = pending.pop(0) if pending else {}
        if pending == []:
            self._pending_tool_args.pop(tool, None)

        self.recorder.add_tool_call(
            tool=tool,
            args=args,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration=duration,
        )

    def on_usage(self, usage: Any) -> None:
        token_usage = self._normalize_usage(usage)
        if token_usage is None:
            return
        for key in self._token_usage:
            self._token_usage[key] += token_usage[key]
        self.recorder.record_token_usage(cast("TokenUsage", self._token_usage.copy()))

    @staticmethod
    def _normalize_args(args: dict[str, Any] | str | None) -> dict[str, Any]:
        if isinstance(args, dict):
            return args
        if args is None:
            return {}
        return {"input": args}

    @staticmethod
    def _normalize_usage(usage: Any) -> dict[str, int] | None:
        if not isinstance(usage, dict):
            return None
        usage_dict = cast("dict[str, Any]", usage)

        prompt_tokens = usage_dict.get("prompt_tokens")
        completion_tokens = usage_dict.get("completion_tokens")

        if prompt_tokens is None:
            prompt_tokens = usage_dict.get("input_tokens", 0)
        if completion_tokens is None:
            completion_tokens = usage_dict.get("output_tokens", 0)

        cache_tokens = int(usage_dict.get("cache_read_input_tokens") or 0) + int(
            usage_dict.get("cache_creation_input_tokens") or 0
        )
        prompt_total = int(prompt_tokens or 0) + cache_tokens
        completion_total = int(completion_tokens or 0)
        total_tokens = int(usage_dict.get("total_tokens") or prompt_total + completion_total)

        return {
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "total_tokens": total_tokens,
        }


def append_event_handler(agent: Any, handler: Any) -> None:
    """Append a handler to an agent while preserving any existing handler."""

    existing = getattr(agent, "event_handler", None)
    agent.event_handler = compose_event_handlers(event_handlers=[h for h in (existing, handler) if h is not None])
