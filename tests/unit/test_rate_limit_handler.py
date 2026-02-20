"""Tests for rate limit error detection and handling."""

from app_operator.rate_limit_handler import (
    detect_rate_limit_error,
    exponential_backoff,
    RateLimitError,
)


class TestRateLimitDetection:
    """Test rate limit error detection from different providers."""

    def test_detect_gemini_429_error(self):
        """Test detection of Gemini 429 error."""
        stderr = 'ApiError: {"error":{"code": 429, "message": "Resource exhausted"}}'
        result = detect_rate_limit_error(stderr, 1, "gemini")
        assert result is not None
        assert isinstance(result, RateLimitError)
        assert result.provider == "gemini"
        assert result.retry_after == 60

    def test_detect_gemini_quota_exceeded(self):
        """Test detection of Gemini quota exceeded error."""
        stderr = "Error: Quota exceeded for this project"
        result = detect_rate_limit_error(stderr, 1, "gemini")
        assert result is not None
        assert result.provider == "gemini"
        assert result.retry_after == 120

    def test_detect_openai_rate_limit(self):
        """Test detection of OpenAI rate limit error."""
        stderr = "Error: Rate limit exceeded (429)"
        result = detect_rate_limit_error(stderr, 1, "openai")
        assert result is not None
        assert result.provider == "openai"
        assert result.retry_after == 60

    def test_detect_anthropic_rate_limit(self):
        """Test detection of Anthropic rate limit error."""
        stderr = "Error 429: rate_limit_error"
        result = detect_rate_limit_error(stderr, 1, "anthropic")
        assert result is not None
        assert result.provider == "anthropic"

    def test_detect_anthropic_overloaded(self):
        """Test detection of Anthropic overloaded error."""
        stderr = "Error: Service is currently overloaded"
        result = detect_rate_limit_error(stderr, 1, "claude")
        assert result is not None
        assert result.provider == "claude"
        assert result.retry_after == 30

    def test_no_rate_limit_error(self):
        """Test that non-rate-limit errors return None."""
        stderr = "Error: Invalid API key"
        result = detect_rate_limit_error(stderr, 1, "gemini")
        assert result is None

    def test_empty_stderr(self):
        """Test that empty stderr returns None."""
        result = detect_rate_limit_error("", 0, "gemini")
        assert result is None


class TestExponentialBackoff:
    """Test exponential backoff calculation."""

    def test_first_attempt(self):
        """Test delay for first retry."""
        delay = exponential_backoff(0, base_delay=5)
        assert delay == 5

    def test_second_attempt(self):
        """Test delay for second retry."""
        delay = exponential_backoff(1, base_delay=5)
        assert delay == 10

    def test_third_attempt(self):
        """Test delay for third retry."""
        delay = exponential_backoff(2, base_delay=5)
        assert delay == 20

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        delay = exponential_backoff(10, base_delay=5, max_delay=100)
        assert delay == 100

    def test_custom_base_delay(self):
        """Test with custom base delay."""
        delay = exponential_backoff(0, base_delay=10)
        assert delay == 10
        delay = exponential_backoff(1, base_delay=10)
        assert delay == 20


class TestRateLimitError:
    """Test RateLimitError exception."""

    def test_create_rate_limit_error(self):
        """Test creating a RateLimitError."""
        error = RateLimitError(
            provider="gemini", message="Rate limit exceeded", retry_after=60
        )
        assert error.provider == "gemini"
        assert error.message == "Rate limit exceeded"
        assert error.retry_after == 60

    def test_rate_limit_error_is_exception(self):
        """Test that RateLimitError is an Exception."""
        error = RateLimitError(provider="test", message="test")
        assert isinstance(error, Exception)


class TestSubprocessErrorLogging:
    """run_subprocess_with_rate_limit_handling logs full stderr on non-zero exit."""

    def test_full_stderr_logged_on_failure(self):
        """stderr is logged in full — not truncated — when subprocess fails."""
        import subprocess
        from unittest.mock import patch
        from app_operator.rate_limit_handler import run_subprocess_with_rate_limit_handling

        long_stderr = "E: " + "x" * 2000  # exceeds any reasonable truncation limit

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=long_stderr
        )

        logged_messages = []

        def capture_error(msg):
            logged_messages.append(msg)

        with (
            patch("subprocess.run", return_value=fake_result),
            patch("app_operator.rate_limit_handler.logger.error", side_effect=capture_error),
        ):
            run_subprocess_with_rate_limit_handling(
                cmd=["fake"],
                provider="gemini",
                max_retries=0,
                operation_name="test_op",
            )

        full_log = " ".join(logged_messages)
        assert long_stderr in full_log, "Full stderr must appear in logs without truncation"
