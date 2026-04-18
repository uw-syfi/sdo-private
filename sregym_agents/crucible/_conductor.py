"""Shared helpers for interacting with the sregym conductor API.

This module centralizes the ``/status`` polling logic previously duplicated
across ``driver._wait_for_stage`` and ``orchestrator._wait_for_mitigation_stage``.

The single public helper is :func:`poll_stage`, which:

* polls ``{api_base}/status`` repeatedly,
* backs off exponentially (1s -> 30s) with a small jitter,
* swallows transient HTTP errors, and
* supports two timeout semantics via ``on_timeout``: raise (``"raise"``,
  default) or warn+return None (``"warn"``).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Literal

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

_INITIAL_DELAY = 1.0
_BACKOFF_FACTOR = 1.5
_MAX_DELAY = 30.0
_HTTP_TIMEOUT = 5.0


async def poll_stage(
    api_base: str,
    *,
    wait_for: Iterable[str] | str,
    timeout: int,
    on_timeout: Literal["raise", "warn"] = "raise",
) -> str | None:
    """Poll the conductor's ``/status`` endpoint until it reports a target stage.

    Args:
        api_base: Base URL of the conductor API (e.g. ``http://localhost:8000``).
        wait_for: Target stage(s). Accepts a single stage name or any iterable
            of stage names (set membership is used to check each poll).
        timeout: Total wall-clock budget in seconds.
        on_timeout: What to do if the timeout is exceeded without a match.
            ``"raise"`` (default) raises ``TimeoutError``; ``"warn"`` logs a
            warning and returns ``None``.

    Returns:
        The matched stage name on success, or ``None`` if the timeout was
        reached and ``on_timeout="warn"``.

    Raises:
        TimeoutError: If the timeout is reached and ``on_timeout="raise"``.
    """
    targets: frozenset[str] = frozenset({wait_for}) if isinstance(wait_for, str) else frozenset(wait_for)
    start = time.monotonic()
    delay = _INITIAL_DELAY

    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(f"{api_base}/status", timeout=_HTTP_TIMEOUT)
                resp.raise_for_status()
                stage = resp.json().get("stage")
                if stage in targets:
                    logger.info("Conductor ready at stage: %r", stage)
                    return stage
                logger.debug("Stage: %r, waiting for %s...", stage, sorted(targets))
            except Exception as e:
                # Polling must survive transient HTTP/connection errors.
                logger.debug("Status check failed: %s", e)

            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)

    msg = f"Conductor did not reach stage {sorted(targets)!r} within {timeout}s"
    if on_timeout == "raise":
        raise TimeoutError(msg)
    logger.warning("%s — proceeding anyway.", msg)
    return None
