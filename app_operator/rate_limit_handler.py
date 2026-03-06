"""Rate limit error detection and handling.

This module provides utilities for detecting and handling rate limit errors
from various LLM API providers (Gemini, OpenAI, Anthropic, etc.) and
implementing retry logic with exponential backoff.
"""

import random
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from app_operator.exceptions import SdsOperatorError
from app_operator.logger import logger

T = TypeVar("T")


@dataclass
class RateLimitError(SdsOperatorError):
    """Exception raised when a rate limit is detected."""

    provider: str
    message: str
    retry_after: int | None = None  # Seconds to wait before retrying


def detect_rate_limit_error(stderr: str, returncode: int, provider: str) -> RateLimitError | None:
    """Detect if an error is due to rate limiting or a transient network failure.

    Args:
        stderr: Standard error output from the command
        returncode: Exit code of the command
        provider: Provider name (gemini, openai, anthropic, etc.)

    Returns:
        RateLimitError if rate limit or transient network error detected, None otherwise
    """
    stderr_lower = stderr.lower()

    # Transient network / DNS errors (provider-agnostic)
    _network_markers = [
        "nameresolutionerror",
        "name or service not known",
        "transporterror",
        "apiconnectionerror",
        "connectionerror",
        "max retries exceeded",
    ]
    if any(m in stderr_lower for m in _network_markers):
        return RateLimitError(
            provider=provider,
            message="Transient network error (DNS/connection failure)",
            retry_after=15,
        )

    # Gemini / Google Vertex AI
    if provider in ["gemini", "vertex"]:
        if re.search(r"\b429\b", stderr) or "resource exhausted" in stderr_lower:
            return RateLimitError(
                provider=provider,
                message="Gemini API rate limit exceeded",
                retry_after=60,
            )
        if "quota" in stderr_lower and "exceeded" in stderr_lower:
            return RateLimitError(
                provider=provider,
                message="Gemini API quota exceeded",
                retry_after=120,
            )

    # OpenAI
    if provider in ["openai", "codex"] and (re.search(r"\b429\b", stderr) or "rate_limit" in stderr_lower):
        return RateLimitError(
            provider=provider,
            message="OpenAI API rate limit exceeded",
            retry_after=60,
        )

    # Anthropic / Claude
    if provider in ["anthropic", "claude", "claude-code"]:
        if re.search(r"\b429\b", stderr) or "rate_limit" in stderr_lower:
            return RateLimitError(
                provider=provider,
                message="Anthropic API rate limit exceeded",
                retry_after=60,
            )
        if "overloaded" in stderr_lower:
            return RateLimitError(
                provider=provider,
                message="Anthropic API overloaded",
                retry_after=30,
            )

    return None


def exponential_backoff(attempt: int, base_delay: int = 5, max_delay: int = 300) -> int:
    """Calculate delay for exponential backoff.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        Delay in seconds
    """
    delay = min(base_delay * (2**attempt), max_delay)
    # Add jitter (±25%) to avoid thundering herd on concurrent retries
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(1, min(int(delay + jitter), max_delay))


def run_with_rate_limit_handling(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: int = 5,
    rate_limit_backoff: int = 60,
    operation_name: str = "operation",
) -> tuple[T | None, bool, str | None]:
    """Run a function with rate limit error handling.

    Args:
        func: Function to execute
        max_retries: Maximum number of retries
        base_delay: Base delay for exponential backoff
        rate_limit_backoff: Additional delay when rate limit detected
        operation_name: Name of operation for logging

    Returns:
        Tuple of (result, success, error_message)
    """
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                logger.info(f"{operation_name} succeeded after {attempt} retry(ies)")
            return result, True, None
        except RateLimitError as e:
            if attempt < max_retries:
                delay = rate_limit_backoff + exponential_backoff(attempt, base_delay)
                logger.warning(
                    f"{operation_name} hit rate limit (attempt {attempt + 1}/{max_retries + 1}). "
                    f"{e.message}. Waiting {delay}s before retry..."
                )
                time.sleep(delay)
            else:
                error_msg = (
                    f"{operation_name} failed after {max_retries + 1} attempts due to rate limiting: {e.message}"
                )
                logger.error(error_msg)
                return None, False, error_msg
        except Exception as e:
            error_msg = f"{operation_name} failed with error: {e!s}"
            logger.error(error_msg)
            return None, False, error_msg

    return None, False, f"{operation_name} failed after {max_retries + 1} attempts"


def run_subprocess_with_rate_limit_handling(
    cmd: list[str],
    provider: str,
    max_retries: int = 3,
    base_delay: int = 5,
    rate_limit_backoff: int = 60,
    operation_name: str = "subprocess",
    **subprocess_kwargs: Any,
) -> tuple[subprocess.CompletedProcess | None, bool, str | None]:
    """Run a subprocess command with rate limit error handling.

    Args:
        cmd: Command to execute
        provider: Provider name for error detection
        max_retries: Maximum number of retries
        base_delay: Base delay for exponential backoff
        rate_limit_backoff: Additional delay when rate limit detected
        operation_name: Name of operation for logging
        **subprocess_kwargs: Additional arguments for subprocess.run

    Returns:
        Tuple of (CompletedProcess result, success, error_message)
    """
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, **subprocess_kwargs)

            # Check for rate limit errors
            stderr = result.stderr or ""
            rate_limit_error = detect_rate_limit_error(stderr, result.returncode, provider)

            if rate_limit_error and attempt < max_retries:
                delay = rate_limit_backoff + exponential_backoff(attempt, base_delay)
                logger.warning(
                    f"{operation_name} hit rate limit (attempt {attempt + 1}/{max_retries + 1}). "
                    f"{rate_limit_error.message}. Waiting {delay}s before retry..."
                )
                time.sleep(delay)
                continue

            if rate_limit_error:
                # Max retries exhausted
                error_msg = (
                    f"{operation_name} failed after {max_retries + 1} attempts "
                    f"due to rate limiting: {rate_limit_error.message}"
                )
                logger.error(error_msg)
                return result, False, error_msg

            # Success or non-rate-limit error
            if result.returncode != 0:
                error_msg = f"{operation_name} failed with exit code {result.returncode}"
                logger.error(error_msg)
                if stderr:
                    logger.error(f"Error output: {stderr}")
                return result, False, error_msg

            if attempt > 0:
                logger.info(f"{operation_name} succeeded after {attempt} retry(ies)")
            return result, True, None

        except Exception as e:
            error_msg = f"{operation_name} raised exception: {e!s}"
            logger.error(error_msg)
            return None, False, error_msg

    return None, False, f"{operation_name} failed after {max_retries + 1} attempts"
