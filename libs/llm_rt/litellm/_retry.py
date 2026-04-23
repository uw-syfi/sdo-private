"""Shared LiteLLM retry helpers for SDS runtimes."""

import time
from typing import Any

import litellm
from loguru import logger

_NETWORK_ERROR_MARKERS = (
    "nameresolutionerror",
    "name or service not known",
    "transporterror",
    "apiconnectionerror",
    "connectionerror",
    "max retries exceeded",
)


def litellm_call_with_retry(
    kwargs: dict[str, Any],
    label: str,
    max_attempts: int = 3,
    token_acc: dict[str, int] | None = None,
) -> str:
    """Call ``litellm.completion`` with retry on transient network errors."""
    for attempt in range(max_attempts):
        try:
            response = litellm.completion(**kwargs)  # type: ignore[reportUnknownMemberType]
            if token_acc is not None:
                usage = getattr(response, "usage", None)
                if usage:
                    token_acc["prompt_tokens"] = token_acc.get("prompt_tokens", 0) + (
                        getattr(usage, "prompt_tokens", 0) or 0
                    )
                    token_acc["completion_tokens"] = token_acc.get("completion_tokens", 0) + (
                        getattr(usage, "completion_tokens", 0) or 0
                    )
                    token_acc["total_tokens"] = token_acc.get("total_tokens", 0) + (
                        getattr(usage, "total_tokens", 0) or 0
                    )
            return response.choices[0].message.content or ""  # type: ignore[reportAttributeAccessIssue]
        except Exception as exc:
            if any(marker in str(exc).lower() for marker in _NETWORK_ERROR_MARKERS) and attempt < max_attempts - 1:
                delay = 15 * (2**attempt)
                logger.warning(
                    f"[LLM] {label}: transient network error (attempt {attempt + 1}/{max_attempts}), "
                    f"retrying in {delay}s: {exc}"
                )
                time.sleep(delay)
            else:
                raise

    return ""  # unreachable; satisfies type checker
