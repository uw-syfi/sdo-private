"""Tests for standalone retry wrappers: arun_with_retry, run_with_retry_sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.test import TestModel

from libs.agent_mw._retry import (
    _compute_delay,
    _is_retryable,
    arun_with_retry,
    run_with_retry_sync,
)

# ---------------------------------------------------------------------------
# _is_retryable
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_429(self):
        exc = ModelHTTPError(status_code=429, model_name="t", body="")
        assert _is_retryable(exc) is True

    def test_500(self):
        exc = ModelHTTPError(status_code=500, model_name="t", body="")
        assert _is_retryable(exc) is True

    def test_503(self):
        exc = ModelHTTPError(status_code=503, model_name="t", body="")
        assert _is_retryable(exc) is True

    def test_400_not_retryable(self):
        exc = ModelHTTPError(status_code=400, model_name="t", body="")
        assert _is_retryable(exc) is False

    def test_non_http_error(self):
        assert _is_retryable(ValueError("boom")) is False


# ---------------------------------------------------------------------------
# _compute_delay
# ---------------------------------------------------------------------------


class TestComputeDelay:
    def test_first_attempt(self):
        d = _compute_delay(0, initial_delay=1.0, backoff_factor=2.0, max_delay=60.0, jitter=False)
        assert d == 1.0

    def test_second_attempt(self):
        d = _compute_delay(1, initial_delay=1.0, backoff_factor=2.0, max_delay=60.0, jitter=False)
        assert d == 2.0

    def test_max_delay_cap(self):
        d = _compute_delay(10, initial_delay=1.0, backoff_factor=2.0, max_delay=5.0, jitter=False)
        assert d == 5.0

    def test_jitter_adds_randomness(self):
        d = _compute_delay(0, initial_delay=1.0, backoff_factor=2.0, max_delay=60.0, jitter=True)
        assert 1.0 <= d <= 2.0


# ---------------------------------------------------------------------------
# arun_with_retry
# ---------------------------------------------------------------------------


def _make_http_error(status: int) -> ModelHTTPError:
    return ModelHTTPError(status_code=status, model_name="test", body="error")


class TestArunWithRetry:
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        agent = Agent(TestModel(call_tools=[]), output_type=str)
        result = await arun_with_retry(agent, "hello")
        assert result.output is not None

    @pytest.mark.asyncio
    async def test_non_retryable_error_propagates(self):
        agent = Agent(TestModel(call_tools=[]), output_type=str)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=_make_http_error(400))
        with patch.object(agent, "iter", return_value=mock_ctx):
            with pytest.raises(ModelHTTPError) as exc_info:
                await arun_with_retry(agent, "hello", max_retries=3)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("libs.agent_mw._retry.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_exhausted_propagates(self, mock_sleep):
        agent = Agent(TestModel(call_tools=[]), output_type=str)

        with patch.object(agent, "iter") as mock_iter:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=_make_http_error(429))
            mock_iter.return_value = mock_ctx
            with pytest.raises(ModelHTTPError):
                await arun_with_retry(agent, "hello", max_retries=2, jitter=False)


# ---------------------------------------------------------------------------
# run_with_retry_sync
# ---------------------------------------------------------------------------


class TestRunWithRetrySync:
    def test_success_no_retry(self):
        agent = Agent(TestModel(call_tools=[]), output_type=str)
        result = run_with_retry_sync(agent, "hello")
        assert result.output is not None

    @patch("libs.agent_mw._retry.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        agent = Agent(TestModel(call_tools=[]), output_type=str)

        call_count = 0
        original_run_sync = agent.run_sync

        def _failing_then_ok(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise _make_http_error(429)
            return original_run_sync(*args, **kwargs)

        with patch.object(agent, "run_sync", side_effect=_failing_then_ok):
            result = run_with_retry_sync(agent, "hello", max_retries=3, jitter=False)
        assert result.output is not None
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @patch("libs.agent_mw._retry.time.sleep")
    def test_non_retryable_propagates(self, mock_sleep):
        agent = Agent(TestModel(call_tools=[]), output_type=str)

        with patch.object(agent, "run_sync", side_effect=_make_http_error(400)):
            with pytest.raises(ModelHTTPError):
                run_with_retry_sync(agent, "hello")
        mock_sleep.assert_not_called()

    @patch("libs.agent_mw._retry.time.sleep")
    def test_retries_exhausted_propagates(self, mock_sleep):
        agent = Agent(TestModel(call_tools=[]), output_type=str)

        with patch.object(agent, "run_sync", side_effect=_make_http_error(429)):
            with pytest.raises(ModelHTTPError):
                run_with_retry_sync(agent, "hello", max_retries=2, jitter=False)
        assert mock_sleep.call_count == 2
