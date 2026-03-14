"""Middleware that logs model responses and tool calls to the console."""

from __future__ import annotations

from typing import Any

from loguru import logger

from libs.pydantic_agent import AgentMiddleware

_MAX_ARG_LEN = 120


def _fmt_args(args: str | dict[str, Any] | None) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args[:_MAX_ARG_LEN] + ("\u2026" if len(args) > _MAX_ARG_LEN else "")
    parts = []
    for k, v in args.items():
        if k in ("content", "new_str"):
            parts.append(f"{k}=<{len(str(v))} chars>")
        else:
            s = str(v)
            parts.append(f"{k}={s[:_MAX_ARG_LEN]!r}" if len(s) > _MAX_ARG_LEN else f"{k}={s!r}")
    return ", ".join(parts)


class ConsoleLoggingMiddleware(AgentMiddleware):
    def on_function_tool_call(self, event: Any) -> None:
        logger.info("[{}] \u2192 {}({})", self._agent.agent_name, event.part.tool_name, _fmt_args(event.part.args))

    def on_part_end(self, event: Any) -> None:
        from pydantic_ai.messages import ThinkingPart

        if isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("[{}] <thinking> {}", self._agent.agent_name, event.part.content)

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        output = result.output
        text = str(output) if not isinstance(output, str) else output
        logger.info("[{}] {}", self._agent.agent_name, text)
