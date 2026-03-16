"""Middleware that logs model responses and tool calls to the console."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from libs.pydantic_agent import AgentMiddleware

if TYPE_CHECKING:
    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder

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


def _fmt_k(n: int | None) -> str:
    if n is None:
        return "?k"
    return f"{round(n / 1000)}k"


def _truncate(s: str, max_len: int = _MAX_ARG_LEN) -> str:
    if len(s) <= max_len:
        return s
    remaining = len(s) - max_len
    return f"{s[:max_len]}\u2026 ({remaining} chars left)"


def _tool_failed(content: Any) -> bool:
    """Return True if a tool returned a failure result dict."""
    return isinstance(content, dict) and not content.get("success", True)


class ConsoleLoggingMiddleware(AgentMiddleware):
    def __init__(
        self,
        context_window: int | None,
        recorder: PydanticAITrajectoryRecorder,
    ) -> None:
        self._context_window = context_window
        self._recorder = recorder

    def _usage_prefix(self) -> str:
        used = _fmt_k(self._recorder.total_usage.input_tokens)
        limit = _fmt_k(self._context_window)
        return f"[{self._agent.agent_name} | {used}/{limit}]"

    def on_function_tool_call(self, event: Any) -> None:
        logger.info(
            "{} \u2192 {}({})",
            self._usage_prefix(),
            event.part.tool_name,
            _fmt_args(event.part.args),
        )

    def on_function_tool_result(self, event: Any) -> None:
        from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

        result = event.result
        prefix = self._usage_prefix()

        if isinstance(result, RetryPromptPart):
            logger.warning(
                "{} \u2717 {}() failed: {}",
                prefix,
                result.tool_name or "unknown",
                result.model_response(),
            )
        elif isinstance(result, ToolReturnPart) and _tool_failed(result.content):
            logger.warning(
                "{} \u2717 {}() exited with code {}: {}",
                prefix,
                result.tool_name,
                result.content.get("exit_code", "?"),
                _truncate(result.content.get("stderr", "")),
            )

    def on_part_end(self, event: Any) -> None:
        from pydantic_ai.messages import ThinkingPart

        if isinstance(event.part, ThinkingPart) and event.part.has_content():
            logger.info("{} <thinking> {}", self._usage_prefix(), event.part.content)

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        output = result.output
        text = str(output) if not isinstance(output, str) else output
        logger.info("[{}] {}", self._agent.agent_name, text)
