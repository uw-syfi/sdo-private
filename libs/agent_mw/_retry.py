"""Retry middleware for transient LLM API errors (429 / 5xx)."""

from __future__ import annotations

import logging
import random

from libs.pydantic_agent._middleware import AgentMiddleware

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429} | set(range(500, 600))


class RetryMiddleware(AgentMiddleware):
    """Retries ``run_sync()`` on transient HTTP errors with exponential backoff.

    Handles ``pydantic_ai.exceptions.ModelHTTPError`` with 429 or 5xx status codes.
    """

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ) -> None:
        self._max_retries = max_retries
        self._initial_delay = initial_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._jitter = jitter
        self._attempt = 0
        self._current_delay = initial_delay

    def before_run(self) -> None:
        """Reset retry state at the start of each ``_run()`` call."""
        self._attempt = 0
        self._current_delay = self._initial_delay

    def on_run_error(self, exc: Exception) -> float | None:
        """Return a delay for retryable errors, or ``None`` to propagate."""
        from pydantic_ai.exceptions import ModelHTTPError

        if not isinstance(exc, ModelHTTPError):
            return None

        status = exc.status_code
        if status not in _RETRYABLE_STATUSES:
            return None

        if self._attempt >= self._max_retries:
            logger.warning(
                "[%s] Max retries (%d) exhausted for status %d — propagating error.",
                self._agent.agent_name,
                self._max_retries,
                status,
            )
            return None

        self._attempt += 1
        delay = min(self._current_delay, self._max_delay)
        if self._jitter:
            delay = delay + random.uniform(0, delay)

        logger.warning(
            "[%s] Retryable error (status %d), attempt %d/%d — retrying in %.1fs.",
            self._agent.agent_name,
            status,
            self._attempt,
            self._max_retries,
            delay,
        )
        self._current_delay = min(self._current_delay * self._backoff_factor, self._max_delay)
        return delay
