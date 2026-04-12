"""Track token usage across DSPy LM calls."""

import dspy


def get_token_usage(lm: dspy.LM | None = None) -> dict:
    """Return aggregated token usage from the LM's call history.

    Each history entry contains a ``usage`` dict with
    ``prompt_tokens``, ``completion_tokens``, and ``total_tokens``.

    Args:
        lm: The DSPy LM instance. Defaults to ``dspy.settings.lm``.

    Returns:
        Dict with prompt_tokens, completion_tokens, total_tokens,
        total_cost, and num_calls.
    """
    lm = lm or dspy.settings.lm
    if lm is None:
        return _empty()

    prompt_tokens = 0
    completion_tokens = 0
    total_cost = 0.0
    num_calls = 0

    for entry in lm.history:
        usage = entry.get("usage", {})
        prompt_tokens += usage.get("prompt_tokens", 0) or 0
        completion_tokens += usage.get("completion_tokens", 0) or 0
        cost = entry.get("cost")
        if cost is not None:
            total_cost += cost
        num_calls += 1

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "total_cost": round(total_cost, 6),
        "num_calls": num_calls,
    }


def clear_history(lm: dspy.LM | None = None) -> None:
    """Clear the LM history so each experiment app starts fresh."""
    lm = lm or dspy.settings.lm
    if lm is not None:
        lm.history.clear()


def _empty() -> dict:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "num_calls": 0,
    }
