"""Shared utilities for pydantic_ai agent classes."""


def _extract_usage(result) -> dict[str, int]:
    """Extract token usage from a RunResult into a plain dict."""
    usage = result.usage()
    return {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "requests": usage.requests or 0,
    }


def _add_usage(acc: dict[str, int], delta: dict[str, int]) -> None:
    """Add delta usage into acc in-place."""
    acc["input_tokens"] += delta["input_tokens"]
    acc["output_tokens"] += delta["output_tokens"]
    acc["requests"] += delta["requests"]
