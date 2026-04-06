"""Middleware that logs per-turn tool calls and final output using stdlib logging."""

from __future__ import annotations

import logging
from typing import Any, cast

from libs.pydantic_agent import AgentMiddleware


def fmt_tool_args(args: str | dict[str, Any] | None) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    parts: list[str] = []
    for k, v in args.items():
        if k in ("content", "new_str"):
            parts.append(f"{k}=<{len(str(v))} chars>")
        else:
            parts.append(f"{k}={str(v)!r}")
    return ", ".join(parts)


def _fmt_k(n: int | None) -> str:
    if n is None:
        return "?k"
    return f"{round(n / 1000)}k"


def tool_call_failed(content: Any) -> bool:
    if not isinstance(content, dict):
        return False
    d = cast("dict[str, Any]", content)
    return not d.get("success", True)


class TurnLoggingMiddleware(AgentMiddleware):
    """Logs tool calls and final output per agent turn using stdlib logging."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        context_window: int | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._context_window = context_window

    def _usage_prefix(self) -> str:
        used = _fmt_k(self._agent.context_window_token_usage)
        limit = _fmt_k(self._context_window)
        return f"[{self._agent.agent_name} | {used}/{limit}]"

    def on_function_tool_call(self, event: Any) -> None:
        self._logger.info(
            "%s \u2192 %s(%s)",
            self._usage_prefix(),
            event.part.tool_name,
            fmt_tool_args(event.part.args),
        )

    def on_function_tool_result(self, event: Any) -> None:
        from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

        result = event.result
        prefix = self._usage_prefix()

        if isinstance(result, RetryPromptPart):
            self._logger.warning(
                "%s \u2717 %s() failed: %s",
                prefix,
                result.tool_name or "unknown",
                result.model_response(),
            )
        elif isinstance(result, ToolReturnPart) and tool_call_failed(result.content):
            self._logger.warning(
                "%s \u2717 %s() exited with code %s: %s",
                prefix,
                result.tool_name,
                result.content.get("exit_code", "?"),
                result.content.get("stderr", ""),
            )
        elif (
            isinstance(result, ToolReturnPart)
            and isinstance(result.content, str)
            and result.content.startswith("Error:")
        ):
            self._logger.warning(
                "%s \u2717 %s(): %s",
                prefix,
                result.tool_name,
                result.content,
            )

    def on_part_end(self, event: Any) -> None:
        from pydantic_ai.messages import ThinkingPart

        if isinstance(event.part, ThinkingPart) and event.part.has_content():
            self._logger.info("%s <thinking> %s", self._usage_prefix(), event.part.content)

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        output = result.output
        text = str(output) if not isinstance(output, str) else output
        self._logger.info("[%s] %s", self._agent.agent_name, text)
