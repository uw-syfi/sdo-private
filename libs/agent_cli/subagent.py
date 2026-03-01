"""Shared subagent primitive for isolated LLM calls.

Provides ``call_subagent()`` — a single fresh litellm call with its own
message list.  Used by both RLM (isolated recursive calls) and the
SubagentCodingAgent (fan-out analysis calls).
"""

import os
import subprocess

from app_operator.logger import logger

from .rlm_agent import _litellm_call_with_retry


def call_subagent(
    model: str,
    system_prompt: str,
    user_prompt: str,
    location: str | None = None,
    token_acc: dict | None = None,
) -> str:
    """Make a completely fresh, isolated litellm call.

    Builds a new ``messages`` list from scratch (system + user), so there is
    no shared conversation history with any other call.

    Args:
        model: litellm-compatible model string (e.g. ``"vertex_ai/gemini-2.0-flash"``).
        system_prompt: System-level instructions for this subagent.
        user_prompt: The user-level task/question.
        location: Optional Vertex AI location forwarded as ``vertex_location``.
        token_acc: Optional dict to accumulate token usage into.

    Returns:
        The assistant's response text.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    kwargs: dict = {
        "model": model,
        "messages": messages,
        "cache": {"no-cache": True},
    }
    loc = location or os.environ.get("VERTEX_LOCATION")
    if loc:
        kwargs["vertex_location"] = loc

    try:
        return _litellm_call_with_retry(
            kwargs, label="subagent call", token_acc=token_acc,
        )
    except KeyboardInterrupt:
        raise
    except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
        logger.error(f"[Subagent] LLM call failed: {type(e).__name__}: {e}")
        return f"Subagent call failed: {type(e).__name__}: {e}"
