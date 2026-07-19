"""Unit tests for benchmarks.sregym.participants.crucible.driver._wait_for_stage."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from benchmarks.sregym.participants.crucible.driver import _wait_for_stage


def _make_response(stage: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"stage": stage}
    return resp


class TestWaitForStage:
    def test_returns_stage_immediately(self):
        """First poll returns a ready stage — no sleep needed."""
        with (
            patch(
                "benchmarks.sregym.participants.crucible.driver.requests.get", return_value=_make_response("diagnosis")
            ),
            patch("benchmarks.sregym.participants.crucible.driver.time.sleep") as mock_sleep,
            patch("benchmarks.sregym.participants.crucible.driver.time.time", side_effect=[0, 0]),
        ):
            stage = _wait_for_stage("http://localhost:8000")
        assert stage == "diagnosis"
        mock_sleep.assert_not_called()

    def test_retries_until_ready_with_increasing_delays(self):
        """Polls non-ready stages first, then succeeds; sleep delays increase."""
        responses = [
            _make_response("pending"),
            _make_response("pending"),
            _make_response("mitigation"),
        ]
        # time.time calls: start, then one per loop iteration check
        times = [0, 0, 1, 2, 3]
        with (
            patch("benchmarks.sregym.participants.crucible.driver.requests.get", side_effect=responses),
            patch("benchmarks.sregym.participants.crucible.driver.time.sleep") as mock_sleep,
            patch("benchmarks.sregym.participants.crucible.driver.time.time", side_effect=times),
            patch("benchmarks.sregym.participants.crucible.driver.random.uniform", return_value=0.0),
        ):
            stage = _wait_for_stage("http://localhost:8000", timeout=300)

        assert stage == "mitigation"
        assert mock_sleep.call_count == 2
        delays = [c.args[0] for c in mock_sleep.call_args_list]
        # First delay is 1.0, second is 1.5
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(1.5)

    def test_raises_timeout_when_stage_never_ready(self):
        """Raises TimeoutError if conductor never reaches a ready stage."""
        # time.time returns 0 then immediately 999 to trigger timeout
        with (
            patch(
                "benchmarks.sregym.participants.crucible.driver.requests.get", return_value=_make_response("pending")
            ),
            patch("benchmarks.sregym.participants.crucible.driver.time.sleep"),
            patch("benchmarks.sregym.participants.crucible.driver.time.time", side_effect=[0, 999]),
        ):
            with pytest.raises(TimeoutError, match="300s"):
                _wait_for_stage("http://localhost:8000", timeout=300)

    def test_continues_on_request_exception(self):
        """A failed request is swallowed and the loop retries until success."""
        responses = [
            requests.ConnectionError("refused"),
            _make_response("diagnosis"),
        ]
        times = [0, 0, 1, 2]
        with (
            patch("benchmarks.sregym.participants.crucible.driver.requests.get", side_effect=responses),
            patch("benchmarks.sregym.participants.crucible.driver.time.sleep") as mock_sleep,
            patch("benchmarks.sregym.participants.crucible.driver.time.time", side_effect=times),
            patch("benchmarks.sregym.participants.crucible.driver.random.uniform", return_value=0.0),
        ):
            stage = _wait_for_stage("http://localhost:8000", timeout=300)

        assert stage == "diagnosis"
        assert mock_sleep.call_count == 1

    def test_delay_capped_at_30_seconds(self):
        """Backoff delay never exceeds 30 seconds regardless of iteration count."""
        # Simulate many non-ready responses; check that sleep never exceeds 30 + jitter
        n = 30
        responses = [_make_response("pending")] * (n - 1) + [_make_response("diagnosis")]
        times = [0] + list(range(n + 1))
        with (
            patch("benchmarks.sregym.participants.crucible.driver.requests.get", side_effect=responses),
            patch("benchmarks.sregym.participants.crucible.driver.time.sleep") as mock_sleep,
            patch("benchmarks.sregym.participants.crucible.driver.time.time", side_effect=times),
            patch("benchmarks.sregym.participants.crucible.driver.random.uniform", return_value=0.0),
        ):
            _wait_for_stage("http://localhost:8000", timeout=9999)

        for c in mock_sleep.call_args_list:
            assert c.args[0] <= 30.0
