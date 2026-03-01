"""Shared utilities for RLM-based agents.

Contains regex patterns and litellm retry helpers used by both
SubagentCodingAgent (in libs/agent_cli) and RLMCodingAgent/HybridCodingAgent
(in app_operator/cli_agent).
"""
import re
import time

import litellm

from loguru import logger

# Patterns that indicate a file-generation task (write specific output files).
# These tasks use a direct single LLM call instead of the RLM loop.
_FILE_GEN_PATTERNS = [
    r"\.sds/code_analysis\.md",
    r"\.sds/deployment_issues\.md",
    r"\.sds/deploy\.sh",
    r"\.sds/health_check\.sh",
]
_FILE_GEN_RE = re.compile("|".join(_FILE_GEN_PATTERNS))

# Patterns that indicate a pure text-generation task (no file writes needed).
# These tasks use a direct single LLM call with the prompt as-is.
_DIRECT_TEXT_PATTERNS = [
    r"fix_summary",
]
_DIRECT_TEXT_RE = re.compile("|".join(_DIRECT_TEXT_PATTERNS))

_NETWORK_ERROR_MARKERS = (
    "nameresolutionerror",
    "name or service not known",
    "transporterror",
    "apiconnectionerror",
    "connectionerror",
    "max retries exceeded",
)


def _litellm_call_with_retry(
    kwargs: dict,
    label: str,
    max_attempts: int = 3,
    token_acc: dict | None = None,
) -> str:
    """Call litellm.completion with retry on transient network errors.

    Returns the response content string, or raises the last exception if all
    attempts fail.  When *token_acc* is provided, prompt/completion/total token
    counts from each successful call are accumulated into it.
    """
    for attempt in range(max_attempts):
        try:
            response = litellm.completion(**kwargs)
            if token_acc is not None:
                usage = getattr(response, "usage", None)
                if usage:
                    token_acc["prompt_tokens"] = token_acc.get(
                        "prompt_tokens", 0) + (getattr(usage, "prompt_tokens", 0) or 0)
                    token_acc["completion_tokens"] = token_acc.get(
                        "completion_tokens", 0) + (getattr(usage, "completion_tokens", 0) or 0)
                    token_acc["total_tokens"] = token_acc.get(
                        "total_tokens", 0) + (getattr(usage, "total_tokens", 0) or 0)
            return response.choices[0].message.content or ""
        except Exception as e:
            if any(m in str(e).lower() for m in _NETWORK_ERROR_MARKERS) and attempt < max_attempts - 1:
                delay = 15 * (2 ** attempt)
                logger.warning(
                    f"[RLM] {label}: transient network error (attempt {attempt + 1}/{max_attempts}), "
                    f"retrying in {delay}s: {e}"
                )
                time.sleep(delay)
            else:
                raise
