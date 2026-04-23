"""Operator-local helpers for isolated subagent calls."""

import os
import subprocess

from loguru import logger

from libs.llm_rt import litellm_call_with_retry


def call_subagent(
    model: str,
    system_prompt: str,
    user_prompt: str,
    location: str | None = None,
    token_acc: dict[str, int] | None = None,
) -> str:
    """Make a fresh isolated LiteLLM call with no shared history."""
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "cache": {"no-cache": True},
    }
    loc = location or os.environ.get("VERTEX_LOCATION")
    if loc:
        kwargs["vertex_location"] = loc

    try:
        return litellm_call_with_retry(kwargs, label="subagent call", token_acc=token_acc)
    except KeyboardInterrupt:
        raise
    except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as exc:
        logger.error(f"[Subagent] LLM call failed: {type(exc).__name__}: {exc}")
        return f"Subagent call failed: {type(exc).__name__}: {exc}"
