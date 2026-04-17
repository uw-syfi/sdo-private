"""Structured progress markers for run_exp.py progress bar tracking."""

from __future__ import annotations

import re

from app_operator.core import logger

_MARKER_PREFIX = "[SDS:PROGRESS]"
_PARSE_RE = re.compile(r"\[SDS:PROGRESS\] phase=(\w+)(.*)")


def emit_progress(phase: str, **kwargs: int) -> None:
    """Emit a structured progress marker to the log."""
    extra = "".join(f" {k}={v}" for k, v in kwargs.items())
    logger.info(f"{_MARKER_PREFIX} phase={phase}{extra}")


def parse_progress(line: str) -> dict[str, str | int] | None:
    """Parse a structured progress marker line.

    Returns a dict with at least {"phase": str} plus any int kwargs,
    or None if the line is not a progress marker.
    """
    m = _PARSE_RE.search(line)
    if not m:
        return None
    result: dict[str, str | int] = {"phase": m.group(1)}
    for kv in m.group(2).split():
        if "=" in kv:
            k, _, v = kv.partition("=")
            try:
                result[k] = int(v)
            except ValueError:
                result[k] = v
    return result
