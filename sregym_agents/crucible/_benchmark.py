"""Shared helpers for parsing benchmark oracle payloads.

These helpers live here (rather than duplicated across orchestrator /
recovery-agent modules) so that extraction logic has a single source of
truth — see GitLab issue #89.
"""

from __future__ import annotations

import json
import re

_ORACLE_RE = re.compile(r"<oracle>\s*(.*?)\s*</oracle>", re.DOTALL)


def extract_benchmark_reasoning(benchmark_block: str, stage: str = "diagnosis") -> str:
    """Extract the ``reasoning`` field from a ``benchmark_result`` oracle block.

    Args:
        benchmark_block: Raw benchmark result block containing ``<oracle>`` tags.
        stage: ``"diagnosis"`` or ``"mitigation"`` — determines which top-level
            key to look up inside the oracle JSON (``Diagnosis`` vs
            ``Mitigation``).

    Returns:
        The ``reasoning`` string if present; an empty string when the oracle
        tag is missing, the JSON is malformed, or the expected keys are
        absent.
    """
    match = _ORACLE_RE.search(benchmark_block)
    if not match:
        return ""
    try:
        data = json.loads(match.group(1))
        key = "Mitigation" if stage == "mitigation" else "Diagnosis"
        return data.get(key, {}).get("reasoning", "")
    except (json.JSONDecodeError, AttributeError):
        return ""
