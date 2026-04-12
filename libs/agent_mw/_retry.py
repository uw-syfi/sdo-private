"""Retry middleware and standalone helpers for transient LLM API errors (429 / 5xx)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any

from pydantic_ai.exceptions import ModelHTTPError

from libs.pydantic_agent import AgentMiddleware, TokenUsage

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.agent import AgentRunResult

    from libs.pydantic_agent import UsageCollector

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429} | set(range(500, 600))


def _is_retryable(exc: Exception) -> bool:
    """Return True if *exc* is a transient ModelHTTPError (429 / 5xx)."""
    return isinstance(exc, ModelHTTPError) and exc.status_code in _RETRYABLE_STATUSES


def _compute_delay(
    attempt: int,
    initial_delay: float,
    backoff_factor: float,
    max_delay: float,
    jitter: bool,
) -> float:
    delay = min(initial_delay * (backoff_factor**attempt), max_delay)
    if jitter:
        delay = delay + random.uniform(0, delay)
    return delay


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
        if not _is_retryable(exc):
            return None

        assert isinstance(exc, ModelHTTPError)
        status = exc.status_code

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


# ---------------------------------------------------------------------------
# Standalone retry wrappers for bare pydantic-ai Agent instances
# ---------------------------------------------------------------------------


async def arun_with_retry(
    agent: Agent[Any, Any],
    prompt: str | None,
    *,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **run_kwargs: Any,
) -> AgentRunResult[Any]:
    """Run a bare pydantic-ai ``Agent`` with retry on transient errors.

    Uses ``agent.iter()`` so that on failure the accumulated message history
    (including completed tool calls) is preserved and the run resumes from
    where it left off rather than restarting from scratch.
    """
    event_stream_handler = run_kwargs.pop("event_stream_handler", None)
    message_history = run_kwargs.pop("message_history", None)
    current_prompt = prompt

    for attempt in range(max_retries + 1):
        agent_run = None
        try:
            async with agent.iter(
                current_prompt,
                message_history=message_history,
                **run_kwargs,
            ) as agent_run:
                async for node in agent_run:
                    if event_stream_handler is not None and (
                        agent.is_model_request_node(node) or agent.is_call_tools_node(node)
                    ):
                        async with node.stream(agent_run.ctx) as stream:
                            await event_stream_handler(agent_run.ctx, stream)
            assert agent_run.result is not None
            return agent_run.result
        except ModelHTTPError as exc:
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            # Capture accumulated messages if available; otherwise restart
            if agent_run is not None:
                message_history = list(agent_run.all_messages())
                current_prompt = None
            delay = _compute_delay(attempt, initial_delay, backoff_factor, max_delay, jitter)
            logger.warning(
                "Retryable error (status %d, attempt %d/%d), resuming in %.1fs.",
                exc.status_code,
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("unreachable")  # pragma: no cover


async def arun_with_retry_tracked(
    agent: Agent[Any, Any],
    prompt: str | None,
    *,
    agent_name: str,
    usage_collector: UsageCollector | None,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **run_kwargs: Any,
) -> AgentRunResult[Any]:
    """Like :func:`arun_with_retry` but reports token usage to a collector.

    Wrapper around plain pydantic-ai ``Agent`` runs (not ``BaseAgent``
    subclasses, which auto-report). After the run completes, builds a
    :class:`TokenUsage` from ``result.usage()`` and adds it to
    ``usage_collector`` under the ``agent_name`` bucket. If
    ``usage_collector`` is ``None``, behaves identically to
    ``arun_with_retry``.
    """
    result = await arun_with_retry(
        agent,
        prompt,
        max_retries=max_retries,
        initial_delay=initial_delay,
        backoff_factor=backoff_factor,
        max_delay=max_delay,
        jitter=jitter,
        **run_kwargs,
    )
    if usage_collector is not None:
        usage_collector.add(agent_name, TokenUsage.from_run_usage(result.usage()))
    return result


def run_with_retry_sync(
    agent: Agent[Any, Any],
    prompt: str | None,
    *,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **run_kwargs: Any,
) -> AgentRunResult[Any]:
    """Synchronous version of :func:`arun_with_retry`.

    Falls back to ``agent.run_sync()`` with a simple restart-on-error loop
    because pydantic-ai has no ``iter_sync()``.  For the sync compaction
    call-sites this is acceptable since those agents are single-turn.
    """
    message_history = run_kwargs.pop("message_history", None)
    current_prompt = prompt

    for attempt in range(max_retries + 1):
        try:
            return agent.run_sync(
                current_prompt,
                message_history=message_history,
                **run_kwargs,
            )
        except ModelHTTPError as exc:
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            current_prompt = prompt
            message_history = None
            delay = _compute_delay(attempt, initial_delay, backoff_factor, max_delay, jitter)
            logger.warning(
                "Retryable error (status %d, attempt %d/%d), retrying in %.1fs.",
                exc.status_code,
                attempt + 1,
                max_retries,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError("unreachable")  # pragma: no cover
