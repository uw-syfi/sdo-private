"""Small HTTP helpers local to the SREGym shared library."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429} | set(range(500, 600))


def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **requests_kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request with retry on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, **requests_kwargs)
            if resp.status_code not in _RETRYABLE_STATUSES:
                resp.raise_for_status()
                return resp
            status = resp.status_code
        except requests.ConnectionError as exc:
            if attempt == max_retries:
                raise
            status = 0
            logger.warning("Connection error for %s %s: %s", method, url, exc)
        else:
            if attempt == max_retries:
                resp.raise_for_status()
                return resp

        delay = min(initial_delay * (backoff_factor**attempt), max_delay)
        if jitter:
            delay = delay + random.uniform(0, delay)
        logger.warning(
            "Retryable HTTP error (status %s, attempt %d/%d) for %s %s, retrying in %.1fs.",
            status,
            attempt + 1,
            max_retries,
            method,
            url,
            delay,
        )
        time.sleep(delay)

    raise RuntimeError("unreachable")  # pragma: no cover
