"""Helpers for constructing safe experiment names.

Experiment directory names are later reused as Docker Compose project names by
generated deploy scripts. Keep names lowercase and limited to characters that
Compose accepts to avoid invalid project-name failures.
"""

import re


def normalize_experiment_token(value: str, fallback: str = "app") -> str:
    """Return a compose-safe token for experiment naming.

    Rules:
    - lowercase
    - only ``a-z``, ``0-9``, ``_`` and ``-``
    - must start with alphanumeric
    """
    lowered = value.lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", lowered)
    cleaned = cleaned.strip("_-")
    if not cleaned:
        return fallback
    if not cleaned[0].isalnum():
        cleaned = f"{fallback}_{cleaned}"
    return cleaned
