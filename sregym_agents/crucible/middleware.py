"""Middleware for the Crucible dual-agent judge loop."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from pydantic_ai.exceptions import ModelRetry

from libs.pydantic_agent._middleware import AgentMiddleware

logger = logging.getLogger(__name__)


class LoopDetectionMiddleware(AgentMiddleware):
    """Detects repeated identical tool calls and nudges the agent to try something new."""

    def __init__(self, max_loop_reminders: int = 3) -> None:
        self._recent_fps: deque = deque(maxlen=3)
        self._loop_reminders: int = 0
        self._max_loop_reminders = max_loop_reminders

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        fp = frozenset([(tool_name, repr(sorted(args.items())))])
        self._recent_fps.append(fp)

        if len(self._recent_fps) == 3 and len(set(self._recent_fps)) == 1:
            if self._loop_reminders >= self._max_loop_reminders:
                logger.warning(
                    "Loop detected (tool=%s) but max reminders reached; allowing through.",
                    tool_name,
                )
                return True
            self._loop_reminders += 1
            logger.warning(
                "Loop detected: tool=%s called identically %d times in a row (reminder %d/%d).",
                tool_name,
                len(self._recent_fps),
                self._loop_reminders,
                self._max_loop_reminders,
            )
            raise ModelRetry(
                "You have been calling the same tool(s) with the same arguments repeatedly "
                "without making progress. Please try a different approach — run a different "
                "command, inspect the system from another angle, or call the submit tool if "
                "you have gathered enough information to submit your answer."
            )

        return True

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        self._recent_fps.clear()
        self._loop_reminders = 0


class TimeoutMiddleware(AgentMiddleware):
    """Reminds the agent to wrap up when it has been running too long."""

    def __init__(
        self,
        timeout_seconds: int = 30 * 60,
        max_timeout_reminders: int = 3,
    ) -> None:
        self._start_time = time.monotonic()
        self._reminders: int = 0
        self._timeout_seconds = timeout_seconds
        self._max_timeout_reminders = max_timeout_reminders

    def before_tool_call(self, tool_name: str, args: dict[str, Any]) -> bool:
        elapsed = time.monotonic() - self._start_time
        if elapsed > self._timeout_seconds:
            if self._reminders >= self._max_timeout_reminders:
                logger.warning(
                    "Timeout exceeded (%.0fs > %ds) but max reminders reached; allowing through.",
                    elapsed,
                    self._timeout_seconds,
                )
                return True
            self._reminders += 1
            logger.warning(
                "Timeout exceeded: %.0fs > %ds (reminder %d/%d).",
                elapsed,
                self._timeout_seconds,
                self._reminders,
                self._max_timeout_reminders,
            )
            raise ModelRetry(
                f"You have been running for over {self._timeout_seconds // 60} minutes. "
                "Please wrap up and call the submit tool now."
            )
        return True


__all__ = ["LoopDetectionMiddleware", "TimeoutMiddleware"]
