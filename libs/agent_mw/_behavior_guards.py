"""Behavior-guard middleware: loop detection, stall detection, thinking repetition, timeout."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from libs.pydantic_agent import AgentMiddleware

logger = logging.getLogger(__name__)


def _make_nudge_message(text: str) -> Any:
    """Wrap *text* as a pydantic-ai UserPromptPart ModelRequest."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return ModelRequest(parts=[UserPromptPart(content=text)])


class LoopDetectionMiddleware(AgentMiddleware):
    """Detects repeated identical tool calls and nudges the agent to try something new."""

    def __init__(self, max_loop_reminders: int = 3) -> None:
        self._recent_fps: deque = deque(maxlen=3)
        self._loop_reminders: int = 0
        self._max_loop_reminders = max_loop_reminders
        self._pending_nudge: str | None = None

    def on_function_tool_call(self, event: Any) -> None:
        tool_name = event.part.tool_name
        args = event.part.args if isinstance(event.part.args, dict) else {}
        fp = frozenset([(tool_name, repr(sorted(args.items())))])
        self._recent_fps.append(fp)

        if len(self._recent_fps) == 3 and len(set(self._recent_fps)) == 1:
            if self._loop_reminders >= self._max_loop_reminders:
                logger.warning(
                    "Loop detected (tool=%s) but max reminders reached; allowing through.",
                    tool_name,
                )
                return
            self._loop_reminders += 1
            logger.warning(
                "Loop detected: tool=%s called identically %d times in a row (reminder %d/%d).",
                tool_name,
                len(self._recent_fps),
                self._loop_reminders,
                self._max_loop_reminders,
            )
            self._pending_nudge = (
                "You have been calling the same tool(s) with the same arguments repeatedly "
                "without making progress. Please try a different approach — run a different "
                "command, inspect the system from another angle, or provide your final answer "
                "if you have gathered enough information."
            )

    def before_model_req_edit_messages(self, ctx: Any, messages: list) -> list:
        if self._pending_nudge:
            messages = list(messages)
            messages.append(_make_nudge_message(self._pending_nudge))
            self._pending_nudge = None
        return messages

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self._recent_fps.clear()
        self._loop_reminders = 0
        self._pending_nudge = None


class ThinkingRepetitionMiddleware(AgentMiddleware):
    """Detects repeated thinking blocks across model responses.

    Monitors ``on_part_end`` for ``ThinkingPart`` events, hashes the first
    200 characters of each block, and flags when duplicates dominate the
    recent window.  After *max_nudges* nudges, strips all tools to force
    the agent to produce its final structured output.
    """

    def __init__(
        self,
        window_size: int = 5,
        duplicate_threshold: int = 3,
        max_nudges: int = 2,
    ) -> None:
        self._window_size = window_size
        self._duplicate_threshold = duplicate_threshold
        self._max_nudges = max_nudges
        self._recent_hashes: deque[int] = deque(maxlen=window_size)
        self._nudge_count: int = 0
        self._pending_nudge: str | None = None
        self._force_submit: bool = False

    def on_part_end(self, event: Any) -> None:
        from pydantic_ai.messages import ThinkingPart

        if not isinstance(event.part, ThinkingPart):
            return
        if not event.part.has_content():
            return
        content = event.part.content or ""
        h = hash(content[:200].strip())
        self._recent_hashes.append(h)

        most_common_count = max(self._recent_hashes.count(v) for v in set(self._recent_hashes))
        if most_common_count >= self._duplicate_threshold:
            if self._nudge_count < self._max_nudges:
                self._nudge_count += 1
                logger.warning(
                    "Thinking repetition detected (%d/%d identical blocks in window). Nudge %d/%d.",
                    most_common_count,
                    len(self._recent_hashes),
                    self._nudge_count,
                    self._max_nudges,
                )
                self._pending_nudge = (
                    "Your recent thinking has been repetitive — you produced the same "
                    "reasoning multiple times. Please take a concrete action: run a tool "
                    "call or provide your final answer."
                )
            else:
                self._force_submit = True
                logger.warning(
                    "Thinking repetition: max nudges exhausted — will strip tools to force submission.",
                )

    def before_model_req_edit_messages(self, ctx: Any, messages: list) -> list:
        if self._pending_nudge:
            messages = list(messages)
            messages.append(_make_nudge_message(self._pending_nudge))
            self._pending_nudge = None
        return messages

    async def before_model_req_edit_tools(self, ctx: Any, tool_defs: list) -> list | None:
        if self._force_submit:
            logger.warning("Thinking repetition: stripping all tools to force submission.")
            return []
        return tool_defs

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self._recent_hashes.clear()
        self._nudge_count = 0
        self._pending_nudge = None
        self._force_submit = False


class StallDetectionMiddleware(AgentMiddleware):
    """Detects when the model produces consecutive responses without any tool calls.

    Conservative design to avoid false positives:
    - **Time gate**: only activates after *min_elapsed_seconds* (default 120 s).
    - **High threshold**: requires *max_no_tool_requests* (default 5) consecutive
      no-tool model requests before nudging.
    - **Generous escalation**: *max_nudges* (default 3) before stripping tools.
    """

    def __init__(
        self,
        max_no_tool_requests: int = 5,
        min_elapsed_seconds: int = 120,
        max_nudges: int = 3,
    ) -> None:
        self._max_no_tool_requests = max_no_tool_requests
        self._min_elapsed_seconds = min_elapsed_seconds
        self._max_nudges = max_nudges
        self._start_time: float = 0.0
        self._consecutive_no_tool: int = 0
        self._current_request_has_tool: bool = False
        self._nudge_count: int = 0
        self._pending_nudge: str | None = None
        self._force_submit: bool = False

    def before_run(self) -> None:
        self._start_time = time.monotonic()
        self._consecutive_no_tool = 0
        self._current_request_has_tool = False
        self._nudge_count = 0
        self._pending_nudge = None
        self._force_submit = False

    def on_function_tool_call(self, event: Any) -> None:
        self._current_request_has_tool = True

    def before_model_req_edit_messages(self, ctx: Any, messages: list) -> list:
        # Skip the very first model request (nothing to evaluate yet).
        if ctx.run_step <= 1:
            return messages

        # Time gate: don't flag early in the run.
        elapsed = time.monotonic() - self._start_time
        if elapsed < self._min_elapsed_seconds:
            self._current_request_has_tool = False
            return messages

        # Evaluate the previous request.
        if self._current_request_has_tool:
            self._consecutive_no_tool = 0
        else:
            self._consecutive_no_tool += 1
        self._current_request_has_tool = False  # reset for next request

        if self._consecutive_no_tool >= self._max_no_tool_requests:
            if self._nudge_count < self._max_nudges:
                self._nudge_count += 1
                logger.warning(
                    "Stall detected: %d consecutive model requests without tool calls (nudge %d/%d).",
                    self._consecutive_no_tool,
                    self._nudge_count,
                    self._max_nudges,
                )
                self._pending_nudge = (
                    f"You have produced {self._consecutive_no_tool} consecutive responses "
                    "without making any tool calls. You appear to be stuck. "
                    "Please either run a tool to make progress, or provide your final answer now."
                )
            else:
                self._force_submit = True
                logger.warning(
                    "Stall: max nudges exhausted — will strip tools to force submission.",
                )

        if self._pending_nudge:
            messages = list(messages)
            messages.append(_make_nudge_message(self._pending_nudge))
            self._pending_nudge = None
        return messages

    async def before_model_req_edit_tools(self, ctx: Any, tool_defs: list) -> list | None:
        if self._force_submit:
            logger.warning("Stall: stripping all tools to force submission.")
            return []
        return tool_defs

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self._consecutive_no_tool = 0
        self._current_request_has_tool = False
        self._nudge_count = 0
        self._pending_nudge = None
        self._force_submit = False


class TimeoutMiddleware(AgentMiddleware):
    """Reminds the agent to wrap up when it has been running too long.

    Checks elapsed time on every model request (not just tool calls), so it
    fires even when the model is stuck in a thinking loop.  After
    *max_timeout_reminders* nudges, strips all tools to force submission.
    """

    def __init__(
        self,
        timeout_seconds: int = 30 * 60,
        max_timeout_reminders: int = 3,
    ) -> None:
        self._start_time = time.monotonic()
        self._reminders: int = 0
        self._timeout_seconds = timeout_seconds
        self._max_timeout_reminders = max_timeout_reminders
        self._force_submit: bool = False

    def before_run(self) -> None:
        self._start_time = time.monotonic()
        self._reminders = 0
        self._force_submit = False

    def before_model_req_edit_messages(self, ctx: Any, messages: list) -> list:
        elapsed = time.monotonic() - self._start_time
        if elapsed <= self._timeout_seconds:
            return messages

        if self._reminders < self._max_timeout_reminders:
            self._reminders += 1
            logger.warning(
                "Timeout exceeded: %.0fs > %ds (reminder %d/%d).",
                elapsed,
                self._timeout_seconds,
                self._reminders,
                self._max_timeout_reminders,
            )
            nudge = (
                f"You have been running for over {int(elapsed) // 60} minutes. "
                "Please wrap up and provide your final answer now."
            )
            messages = list(messages)
            messages.append(_make_nudge_message(nudge))
        else:
            self._force_submit = True
            logger.warning(
                "Timeout: max reminders exhausted — will strip tools to force submission.",
            )

        return messages

    async def before_model_req_edit_tools(self, ctx: Any, tool_defs: list) -> list | None:
        if self._force_submit:
            logger.warning("Timeout: stripping all tools to force submission.")
            return []
        return tool_defs


__all__ = [
    "LoopDetectionMiddleware",
    "StallDetectionMiddleware",
    "ThinkingRepetitionMiddleware",
    "TimeoutMiddleware",
]
