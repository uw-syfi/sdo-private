"""Unit tests for sregym_agents.crucible.middleware."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic_ai.exceptions import ModelRetry

from sregym_agents.crucible.middleware import LoopDetectionMiddleware, TimeoutMiddleware

# ---------------------------------------------------------------------------
# LoopDetectionMiddleware
# ---------------------------------------------------------------------------


class TestLoopDetectionMiddleware:
    def _call(self, mw: LoopDetectionMiddleware, n: int = 1, tool: str = "t", args: dict | None = None):
        if args is None:
            args = {"cmd": "ls"}
        for _ in range(n):
            mw.before_tool_call(tool, args)

    def test_two_identical_calls_do_not_raise(self):
        mw = LoopDetectionMiddleware()
        self._call(mw, 2)  # should not raise

    def test_three_identical_calls_raises_model_retry(self):
        mw = LoopDetectionMiddleware()
        with pytest.raises(ModelRetry):
            self._call(mw, 3)

    def test_three_different_calls_do_not_raise(self):
        mw = LoopDetectionMiddleware()
        mw.before_tool_call("tool1", {"a": "1"})
        mw.before_tool_call("tool2", {"a": "2"})
        mw.before_tool_call("tool3", {"a": "3"})

    def test_after_max_reminders_allows_through(self):
        mw = LoopDetectionMiddleware(max_loop_reminders=1)
        # First triple raises (reminder 1/1)
        with pytest.raises(ModelRetry):
            self._call(mw, 3)
        # Now at max_loop_reminders; next identical triple should allow through
        result = mw.before_tool_call("t", {"cmd": "ls"})
        assert result is True

    def test_after_run_clears_history(self):
        mw = LoopDetectionMiddleware()
        # Call twice
        self._call(mw, 2)
        # Clear state
        mw.after_run(None)
        # Now three identical calls should raise again (deque was cleared)
        with pytest.raises(ModelRetry):
            self._call(mw, 3)


# ---------------------------------------------------------------------------
# TimeoutMiddleware
# ---------------------------------------------------------------------------


class TestTimeoutMiddleware:
    def test_within_timeout_returns_true(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 5.0]):
            mw = TimeoutMiddleware(timeout_seconds=60)
            result = mw.before_tool_call("tool", {})
        assert result is True

    def test_past_timeout_first_call_raises_model_retry(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            with pytest.raises(ModelRetry):
                mw.before_tool_call("tool", {})

    def test_past_timeout_at_max_reminders_raises(self):
        times = [0.0] + [3700.0] * 3
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=3)
            for _ in range(2):
                with pytest.raises(ModelRetry):
                    mw.before_tool_call("tool", {})
            # Third call is at max
            with pytest.raises(ModelRetry):
                mw.before_tool_call("tool", {})

    def test_past_timeout_exceeding_max_reminders_allows_through(self):
        times = [0.0] + [3700.0] * 10
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=2)
            # Exhaust reminders
            for _ in range(2):
                with pytest.raises(ModelRetry):
                    mw.before_tool_call("tool", {})
            # Now past max — should return True
            result = mw.before_tool_call("tool", {})
        assert result is True
