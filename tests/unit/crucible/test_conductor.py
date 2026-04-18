"""Unit tests for sregym_agents.crucible._conductor.poll_stage.

``poll_stage`` is the single shared polling helper used by both the driver
(``_wait_for_stage``) and the orchestrator (``_wait_for_mitigation_stage``).
Its behavior must cover both prior sites:

* driver: exponential backoff 1s -> 30s with jitter, raise on timeout
* orchestrator: same backoff, warn + return None on timeout
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sregym_agents.crucible._conductor import poll_stage

if TYPE_CHECKING:
    from collections.abc import Sequence


class _FakeClient:
    """Minimal async context-manager wrapping a queue of poll outcomes.

    Each item in ``responses`` is either a stage string (the mocked
    ``/status`` response) or an Exception instance to raise from ``.get``.
    """

    def __init__(self, responses: Sequence[object]):
        self._responses: list[object] = list(responses)
        self.calls: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    async def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, str)
        return _FakeResponse(item)


class _FakeResponse:
    def __init__(self, stage: str) -> None:
        self._stage = stage

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"stage": self._stage}


class TestPollStageHappyPath:
    async def test_returns_on_first_match_string_wait_for(self) -> None:
        client = _FakeClient(["diagnosis"])
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
        ):
            stage = await poll_stage(
                "http://localhost:8000",
                wait_for="diagnosis",
                timeout=300,
            )
        assert stage == "diagnosis"
        sleep_mock.assert_not_called()
        assert client.calls[0] == "http://localhost:8000/status"

    async def test_returns_on_first_match_iterable_wait_for(self) -> None:
        client = _FakeClient(["mitigation"])
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
        ):
            stage = await poll_stage(
                "http://localhost:8000",
                wait_for={"diagnosis", "mitigation"},
                timeout=300,
            )
        assert stage == "mitigation"
        sleep_mock.assert_not_called()

    async def test_polls_until_match(self) -> None:
        """Non-matching stages cause sleep + retry until a match appears."""
        client = _FakeClient(["pending", "pending", "mitigation"])
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
        ):
            stage = await poll_stage(
                "http://localhost:8000",
                wait_for="mitigation",
                timeout=300,
            )
        assert stage == "mitigation"
        assert sleep_mock.call_count == 2


class TestPollStageBackoff:
    async def test_exponential_backoff_progression(self) -> None:
        """Sleep delays follow 1s -> 1.5s -> ... progression (without jitter)."""
        client = _FakeClient(["pending"] * 4 + ["diagnosis"])
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
        ):
            await poll_stage(
                "http://localhost:8000",
                wait_for="diagnosis",
                timeout=300,
            )
        delays = [c.args[0] for c in sleep_mock.call_args_list]
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(1.5)
        assert delays[2] == pytest.approx(2.25)
        assert delays[3] == pytest.approx(3.375)

    async def test_delay_capped_at_30s(self) -> None:
        """Backoff never exceeds 30s."""
        n = 30
        responses: Sequence[object] = ["pending"] * (n - 1) + ["diagnosis"]
        client = _FakeClient(responses)
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
        ):
            await poll_stage(
                "http://localhost:8000",
                wait_for="diagnosis",
                timeout=100_000,
            )
        for c in sleep_mock.call_args_list:
            assert c.args[0] <= 30.0

    async def test_jitter_applied(self) -> None:
        """random.uniform is used to add jitter proportional to current delay."""
        client = _FakeClient(["pending", "diagnosis"])
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.05) as mock_uniform,
        ):
            await poll_stage(
                "http://localhost:8000",
                wait_for="diagnosis",
                timeout=300,
            )
        assert mock_uniform.called
        # Delay should be base (1.0) + jitter (0.05) = 1.05
        first_delay = sleep_mock.call_args_list[0].args[0]
        assert first_delay == pytest.approx(1.05)


class TestPollStageTimeoutRaise:
    async def test_raises_timeout_error_by_default(self) -> None:
        client = _FakeClient(["pending"] * 1000)
        times = iter([0.0] + [999.0] * 100)
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
            patch("sregym_agents.crucible._conductor.time.monotonic", lambda: next(times)),
        ):
            with pytest.raises(TimeoutError, match="300s"):
                await poll_stage(
                    "http://localhost:8000",
                    wait_for="diagnosis",
                    timeout=300,
                )

    async def test_raises_when_on_timeout_raise(self) -> None:
        client = _FakeClient(["pending"] * 1000)
        times = iter([0.0] + [999.0] * 100)
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
            patch("sregym_agents.crucible._conductor.time.monotonic", lambda: next(times)),
        ):
            with pytest.raises(TimeoutError):
                await poll_stage(
                    "http://localhost:8000",
                    wait_for="diagnosis",
                    timeout=60,
                    on_timeout="raise",
                )


class TestPollStageTimeoutWarn:
    async def test_returns_none_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _FakeClient(["pending"] * 1000)
        times = iter([0.0] + [999.0] * 100)
        sleep_mock = AsyncMock()
        caplog.set_level(logging.WARNING, logger="sregym_agents.crucible._conductor")
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
            patch("sregym_agents.crucible._conductor.time.monotonic", lambda: next(times)),
        ):
            result = await poll_stage(
                "http://localhost:8000",
                wait_for="mitigation",
                timeout=300,
                on_timeout="warn",
            )
        assert result is None
        messages = [rec.getMessage() for rec in caplog.records]
        assert any("300s" in m for m in messages), messages


class TestPollStageSwallowsTransientErrors:
    async def test_continues_on_request_exception(self) -> None:
        responses: Sequence[object] = [httpx.ConnectError("refused"), "diagnosis"]
        client = _FakeClient(responses)
        sleep_mock = AsyncMock()
        with (
            patch("sregym_agents.crucible._conductor.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible._conductor.asyncio.sleep", sleep_mock),
            patch("sregym_agents.crucible._conductor.random.uniform", return_value=0.0),
        ):
            stage = await poll_stage(
                "http://localhost:8000",
                wait_for="diagnosis",
                timeout=300,
            )
        assert stage == "diagnosis"
        assert sleep_mock.call_count == 1
