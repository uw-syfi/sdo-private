"""Middleware that logs model responses and tool calls to the console."""

from __future__ import annotations

from typing import Any

from loguru import logger

from libs.pydantic_agent import AgentMiddleware

_MAX_ARG_LEN = 120
_MAX_RESULT_LEN = 300


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


def _fmt_result(result: Any) -> str:
    if isinstance(result, dict):
        rc = result.get("returncode", "?")
        stdout = str(result.get("stdout", ""))
        truncated = len(stdout) - _MAX_RESULT_LEN
        suffix = f" [{truncated} chars truncated]" if truncated > 0 else ""
        return f"rc={rc} stdout=\n{stdout[:_MAX_RESULT_LEN]!r}{suffix}"
    s = str(result) if result is not None else "<none>"
    truncated = len(s) - _MAX_RESULT_LEN
    suffix = f" [{truncated} chars truncated]" if truncated > 0 else ""
    return f"\n{s[:_MAX_RESULT_LEN]}{suffix}"


class ConsoleLoggingMiddleware(AgentMiddleware):
    def on_function_tool_call(self, event: Any) -> None:
        logger.info("[{}] \u2192 {}({})", self._agent.agent_name, event.part.tool_name, _fmt_args(event.part.args))

    def on_function_tool_result(self, event: Any) -> None:
        logger.info(
            "[{}] \u2190 {}: {}",
            self._agent.agent_name,
            event.result.tool_name,
            _fmt_result(event.result.content),
        )

    def on_part_end(self, event: Any) -> None:
        from pydantic_ai.messages import ThinkingPart

        if isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.debug("[{}] <thinking> {}", self._agent.agent_name, event.part.content)

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        output = result.output
        text = str(output) if not isinstance(output, str) else output
        logger.info("[{}] {}", self._agent.agent_name, text[:500] + ("\u2026" if len(text) > 500 else ""))
