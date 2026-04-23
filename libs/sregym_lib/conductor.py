"""Client for the SREGym conductor HTTP API.

All agents (crucible, cli_agent, any future variant) share the same
conductor contract — this module owns it.

Covered surface:

* API base URL resolution from ``API_HOSTNAME`` / ``API_PORT`` env vars.
* Status-stage polling with exponential backoff (async primary,
  :func:`poll_stage_sync` wrapper for sync callers).
* One-shot GETs for ``/get_app``, ``/get_problem``, ``/stages`` — each
  uses :func:`libs.sregym_lib._http.request_with_retry` so transient failures
  don't propagate to drivers.
* Best-effort ``POST /cleanup`` for the deferred-teardown gate.

Endpoint paths and stage-set membership constants live in
:mod:`libs.sregym_lib.schema`; use those rather than hardcoding strings.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import TYPE_CHECKING, Any, Literal

import httpx
import requests

from libs.sregym_lib._http import request_with_retry
from libs.sregym_lib.schema import (
    API_HOSTNAME_DEFAULT,
    API_HOSTNAME_ENV,
    API_PORT_DEFAULT,
    API_PORT_ENV,
    CLEANUP_ENDPOINT,
    GET_APP_ENDPOINT,
    GET_PROBLEM_ENDPOINT,
    STAGES_ENDPOINT,
    STATUS_ENDPOINT,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

_INITIAL_DELAY = 1.0
_BACKOFF_FACTOR = 1.5
_MAX_DELAY = 30.0
_HTTP_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# API base
# ---------------------------------------------------------------------------


def get_api_base() -> str:
    """Return ``http://{API_HOSTNAME}:{API_PORT}`` (with defaults).

    All agents read these two env vars identically; centralize here so
    the convention is defined in one place.
    """
    host = os.getenv(API_HOSTNAME_ENV, API_HOSTNAME_DEFAULT)
    port = os.getenv(API_PORT_ENV, API_PORT_DEFAULT)
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Stage polling
# ---------------------------------------------------------------------------


async def poll_stage(
    api_base: str,
    *,
    wait_for: Iterable[str] | str,
    timeout: int,
    on_timeout: Literal["raise", "warn"] = "raise",
) -> str | None:
    """Poll ``/status`` until the conductor reports one of ``wait_for``.

    Args:
        api_base: Conductor base URL (e.g. ``http://localhost:8000``).
        wait_for: Target stage(s). A single name or any iterable of names.
        timeout: Total wall-clock budget in seconds.
        on_timeout: ``"raise"`` (default) raises ``TimeoutError``;
            ``"warn"`` logs a warning and returns ``None``.

    Returns:
        Matched stage name, or ``None`` when timed out with ``on_timeout="warn"``.
    """
    targets: frozenset[str] = frozenset({wait_for}) if isinstance(wait_for, str) else frozenset(wait_for)
    start = time.monotonic()
    delay = _INITIAL_DELAY

    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(f"{api_base}{STATUS_ENDPOINT}", timeout=_HTTP_TIMEOUT)
                resp.raise_for_status()
                stage = resp.json().get("stage")
                if stage in targets:
                    logger.info("Conductor ready at stage: %r", stage)
                    return stage
                logger.debug("Stage: %r, waiting for %s...", stage, sorted(targets))
            except Exception as e:
                logger.debug("Status check failed: %s", e)

            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)

    msg = f"Conductor did not reach stage {sorted(targets)!r} within {timeout}s"
    if on_timeout == "raise":
        raise TimeoutError(msg)
    logger.warning("%s — proceeding anyway.", msg)
    return None


def poll_stage_sync(
    api_base: str,
    *,
    wait_for: Iterable[str] | str,
    timeout: int,
    on_timeout: Literal["raise", "warn"] = "raise",
) -> str | None:
    """Synchronous wrapper around :func:`poll_stage`.

    Creates a fresh event loop per call via ``asyncio.run`` — fine for
    the driver-level usage this is intended for (a handful of polls per
    run, not a hot loop).  Must not be called from within a running
    event loop.
    """
    return asyncio.run(poll_stage(api_base, wait_for=wait_for, timeout=timeout, on_timeout=on_timeout))


async def wait_for_stages_or_last_seen(
    api_base: str,
    *,
    expected: Iterable[str],
    timeout: int,
) -> str | None:
    """Poll ``/status`` until one of ``expected`` is observed, or timeout.

    On timeout, returns the **last observed** stage rather than ``None``
    so callers can log exactly where the conductor got stuck.  Returns
    ``None`` only if no poll ever succeeded.

    This is the cli_agent post-stage pattern (see
    ``sregym_agents.crucible.orchestrator._wait_for_mitigation_stage``
    for the diagnostic history).
    """
    targets = frozenset(expected)
    start = time.monotonic()
    delay = _INITIAL_DELAY
    last_seen: str | None = None

    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(f"{api_base}{STATUS_ENDPOINT}", timeout=_HTTP_TIMEOUT)
                resp.raise_for_status()
                stage = resp.json().get("stage")
                if stage is not None:
                    last_seen = str(stage)
                    if last_seen in targets:
                        return last_seen
            except Exception as e:
                logger.debug("Status check failed: %s", e)

            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY / 2)

    logger.warning(
        "timed out after %ds waiting for conductor to reach %s; last stage: %r",
        timeout,
        sorted(targets),
        last_seen,
    )
    return last_seen


def wait_for_stages_or_last_seen_sync(
    api_base: str,
    *,
    expected: Iterable[str],
    timeout: int,
) -> str | None:
    """Synchronous wrapper around :func:`wait_for_stages_or_last_seen`."""
    return asyncio.run(wait_for_stages_or_last_seen(api_base, expected=expected, timeout=timeout))


async def get_current_stage(api_base: str) -> str | None:
    """Fetch the conductor's current stage once (no polling).

    Returns ``None`` on any error — callers decide whether that's fatal.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{api_base}{STATUS_ENDPOINT}", timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            stage = resp.json().get("stage")
            return str(stage) if stage is not None else None
    except Exception as e:
        logger.debug("Failed to query conductor stage: %s", e)
        return None


def get_current_stage_sync(api_base: str) -> str | None:
    """Synchronous one-shot status fetch."""
    try:
        resp = requests.get(f"{api_base}{STATUS_ENDPOINT}", timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        stage = resp.json().get("stage")
        return str(stage) if stage is not None else None
    except Exception as e:
        logger.debug("Failed to query conductor stage: %s", e)
        return None


# ---------------------------------------------------------------------------
# One-shot GETs
# ---------------------------------------------------------------------------


def get_app_info(api_base: str) -> dict[str, Any]:
    """Fetch ``/get_app`` → ``{"app_name": ..., "namespace": ...}``."""
    resp = request_with_retry("GET", f"{api_base}{GET_APP_ENDPOINT}", timeout=10)
    return resp.json()


def get_problem_id(api_base: str) -> str:
    """Fetch ``/get_problem`` → problem_id."""
    resp = request_with_retry("GET", f"{api_base}{GET_PROBLEM_ENDPOINT}", timeout=10)
    return resp.json()["problem_id"]


def get_planned_stages(api_base: str) -> list[str]:
    """Fetch ``/stages`` → list of planned stage names."""
    resp = request_with_retry("GET", f"{api_base}{STAGES_ENDPOINT}", timeout=10)
    return resp.json().get("stages", [])


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def signal_cleanup(api_base: str) -> None:
    """Best-effort ``POST /cleanup`` — never raises.

    Used by deferred-teardown agents (e.g. crucible) to release the
    conductor's teardown gate.  Cleanup failure must not mask the driver's
    real exit path.
    """
    try:
        resp = requests.post(f"{api_base}{CLEANUP_ENDPOINT}", timeout=60)
        logger.info("POST /cleanup -> status=%s body=%s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("POST /cleanup failed: %s", e)
