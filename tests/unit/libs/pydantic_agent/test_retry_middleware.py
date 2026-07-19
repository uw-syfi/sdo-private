# pyright: reportPrivateUsage=false
"""Tests for RetryMiddleware and the on_run_error hook."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.test import TestModel

from libs.agent_mw._retry import RetryMiddleware
from libs.pydantic_agent import AgentMiddleware, BaseAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def echo(ctx: Any, message: str) -> str:
    return f"echo: {message}"


def _make_agent(middleware: list[AgentMiddleware] | None = None, agent_name: str = "Test Agent") -> BaseAgent[Any]:
    class ConcreteAgent(BaseAgent[Any]):
        def __init__(self):
            super().__init__(None, agent_name=agent_name, middleware=middleware or [])
            self._agent = Agent(
                TestModel(call_tools=[]),
                deps_type=type(None),
                output_type=str,
            )

    return ConcreteAgent()


def _make_http_error(status_code: int) -> ModelHTTPError:
    """Create a ModelHTTPError with the given status code."""
    exc = ModelHTTPError(status_code=status_code, model_name="test", body="error")
    return exc


# ---------------------------------------------------------------------------
# on_run_error: retryable status codes
# ---------------------------------------------------------------------------


class TestOnRunError:
    def test_returns_delay_for_429(self):
        mw = RetryMiddleware(jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))
        delay = mw.on_run_error(_make_http_error(429))
        assert delay is not None
        assert delay > 0

    def test_returns_delay_for_500(self):
        mw = RetryMiddleware(jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(_make_http_error(500)) is not None

    def test_returns_delay_for_502(self):
        mw = RetryMiddleware(jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(_make_http_error(502)) is not None

    def test_returns_delay_for_503(self):
        mw = RetryMiddleware(jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(_make_http_error(503)) is not None

    def test_returns_none_for_400(self):
        mw = RetryMiddleware()
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(_make_http_error(400)) is None

    def test_returns_none_for_401(self):
        mw = RetryMiddleware()
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(_make_http_error(401)) is None

    def test_returns_none_for_non_http_error(self):
        mw = RetryMiddleware()
        mw.on_attach(_make_agent(middleware=[mw]))
        assert mw.on_run_error(ValueError("boom")) is None


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------


class TestBackoff:
    def test_delay_doubles_each_attempt(self):
        mw = RetryMiddleware(initial_delay=1.0, backoff_factor=2.0, jitter=False, max_retries=5)
        mw.on_attach(_make_agent(middleware=[mw]))

        d1 = mw.on_run_error(_make_http_error(429))
        d2 = mw.on_run_error(_make_http_error(429))
        d3 = mw.on_run_error(_make_http_error(429))

        assert d1 == 1.0
        assert d2 == 2.0
        assert d3 == 4.0

    def test_max_delay_capping(self):
        mw = RetryMiddleware(initial_delay=10.0, backoff_factor=2.0, max_delay=15.0, jitter=False, max_retries=5)
        mw.on_attach(_make_agent(middleware=[mw]))

        d1 = mw.on_run_error(_make_http_error(429))
        d2 = mw.on_run_error(_make_http_error(429))

        assert d1 == 10.0
        assert d2 == 15.0  # capped

    def test_max_retries_exhaustion(self):
        mw = RetryMiddleware(max_retries=2, jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))

        assert mw.on_run_error(_make_http_error(429)) is not None
        assert mw.on_run_error(_make_http_error(429)) is not None
        assert mw.on_run_error(_make_http_error(429)) is None  # exhausted

    def test_jitter_adds_randomness(self):
        mw = RetryMiddleware(initial_delay=1.0, jitter=True, max_retries=5)
        mw.on_attach(_make_agent(middleware=[mw]))

        delay = mw.on_run_error(_make_http_error(429))
        # With jitter, delay is in [base_delay, 2*base_delay]
        assert delay is not None
        assert 1.0 <= delay <= 2.0


# ---------------------------------------------------------------------------
# before_run resets state
# ---------------------------------------------------------------------------


class TestBeforeRunReset:
    def test_resets_attempt_counter(self):
        mw = RetryMiddleware(max_retries=1, jitter=False)
        mw.on_attach(_make_agent(middleware=[mw]))

        assert mw.on_run_error(_make_http_error(429)) is not None
        assert mw.on_run_error(_make_http_error(429)) is None  # exhausted

        mw.before_run()  # reset

        assert mw.on_run_error(_make_http_error(429)) is not None  # works again

    def test_resets_delay(self):
        mw = RetryMiddleware(initial_delay=1.0, backoff_factor=2.0, jitter=False, max_retries=5)
        mw.on_attach(_make_agent(middleware=[mw]))

        mw.on_run_error(_make_http_error(429))  # 1.0, bumps to 2.0
        mw.on_run_error(_make_http_error(429))  # 2.0, bumps to 4.0

        mw.before_run()

        delay = mw.on_run_error(_make_http_error(429))
        assert delay == 1.0  # back to initial


# ---------------------------------------------------------------------------
# Integration: retry loop in BaseAgent._arun()
# ---------------------------------------------------------------------------


class TestRetryIntegration:
    @pytest.mark.asyncio
    @patch("libs.pydantic_agent._base.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_then_succeed(self, mock_sleep: Any):
        mw = RetryMiddleware(max_retries=3, jitter=False, initial_delay=0.1)
        agent = _make_agent(middleware=[mw])

        call_count = 0
        original_iter = agent._agent.iter

        def _patched_iter(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Return a context manager that raises on __aenter__
                class _Failing:
                    async def __aenter__(self) -> None:
                        raise _make_http_error(429)

                    async def __aexit__(self, *exc: object) -> bool:
                        return False

                return _Failing()
            return original_iter(*args, **kwargs)

        agent._agent.iter = _patched_iter
        result = await agent._arun("hi")
        assert result is not None
        assert call_count == 3
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    @patch("libs.pydantic_agent._base.asyncio.sleep", new_callable=AsyncMock)
    async def test_non_retryable_error_propagates(self, mock_sleep: Any):
        mw = RetryMiddleware()
        agent = _make_agent(middleware=[mw])

        class _AlwaysFailing:
            async def __aenter__(self) -> None:
                raise ValueError("not retryable")

            async def __aexit__(self, *exc: object) -> bool:
                return False

        def always_failing(*_args: Any, **_kwargs: Any) -> Any:
            return _AlwaysFailing()

        agent._agent.iter = always_failing

        with pytest.raises(ValueError, match="not retryable"):
            await agent._arun("hi")
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("libs.pydantic_agent._base.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_exhausted_propagates(self, mock_sleep: Any):
        mw = RetryMiddleware(max_retries=2, jitter=False)
        agent = _make_agent(middleware=[mw])

        class _AlwaysFailing:
            async def __aenter__(self) -> None:
                raise _make_http_error(429)

            async def __aexit__(self, *exc: object) -> bool:
                return False

        def always_failing(*_args: Any, **_kwargs: Any) -> Any:
            return _AlwaysFailing()

        agent._agent.iter = always_failing

        with pytest.raises(ModelHTTPError):
            await agent._arun("hi")
        assert mock_sleep.await_count == 2


# ---------------------------------------------------------------------------
# Default on_run_error in base AgentMiddleware
# ---------------------------------------------------------------------------


class TestBaseMiddlewareOnRunError:
    def test_default_returns_none(self):
        mw = AgentMiddleware()
        assert mw.on_run_error(Exception("test")) is None
