"""Unit tests for sregym_agents.crucible.middleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelRetry

from sregym_agents.crucible.middleware import LoopDetectionMiddleware, TimeoutMiddleware


def _make_event(tool_name: str, args: dict | None = None) -> MagicMock:
    event = MagicMock()
    event.part.tool_name = tool_name
    event.part.args = args if args is not None else {"cmd": "ls"}
    return event


# ---------------------------------------------------------------------------
# LoopDetectionMiddleware
# ---------------------------------------------------------------------------


class TestLoopDetectionMiddleware:
    def _call(self, mw: LoopDetectionMiddleware, n: int = 1, tool: str = "t", args: dict | None = None):
        if args is None:
            args = {"cmd": "ls"}
        for _ in range(n):
            mw.on_function_tool_call(_make_event(tool, args))

    def test_two_identical_calls_do_not_raise(self):
        mw = LoopDetectionMiddleware()
        self._call(mw, 2)  # should not raise

    def test_three_identical_calls_raises_model_retry(self):
        mw = LoopDetectionMiddleware()
        with pytest.raises(ModelRetry):
            self._call(mw, 3)

    def test_three_different_calls_do_not_raise(self):
        mw = LoopDetectionMiddleware()
        mw.on_function_tool_call(_make_event("tool1", {"a": "1"}))
        mw.on_function_tool_call(_make_event("tool2", {"a": "2"}))
        mw.on_function_tool_call(_make_event("tool3", {"a": "3"}))

    def test_after_max_reminders_allows_through(self):
        mw = LoopDetectionMiddleware(max_loop_reminders=1)
        # First triple raises (reminder 1/1)
        with pytest.raises(ModelRetry):
            self._call(mw, 3)
        # Now at max_loop_reminders; next identical call should allow through (no raise)
        mw.on_function_tool_call(_make_event("t", {"cmd": "ls"}))

    def test_after_run_clears_history(self):
        mw = LoopDetectionMiddleware()
        # Call twice
        self._call(mw, 2)
        # Clear state
        mw.after_run(None)
        # Now three identical calls should raise again (deque was cleared)
        with pytest.raises(ModelRetry):
            self._call(mw, 3)

    def test_string_args_treated_as_empty_dict(self):
        mw = LoopDetectionMiddleware()
        event = _make_event("tool")
        event.part.args = "raw string args"
        # Should not raise TypeError
        mw.on_function_tool_call(event)


# ---------------------------------------------------------------------------
# TimeoutMiddleware
# ---------------------------------------------------------------------------


class TestTimeoutMiddleware:
    def test_within_timeout_returns_none(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 5.0]):
            mw = TimeoutMiddleware(timeout_seconds=60)
            result = mw.on_function_tool_call(_make_event("tool"))
        assert result is None

    def test_past_timeout_first_call_raises_model_retry(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            with pytest.raises(ModelRetry):
                mw.on_function_tool_call(_make_event("tool"))

    def test_past_timeout_at_max_reminders_raises(self):
        times = [0.0] + [3700.0] * 3
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=3)
            for _ in range(2):
                with pytest.raises(ModelRetry):
                    mw.on_function_tool_call(_make_event("tool"))
            # Third call is at max
            with pytest.raises(ModelRetry):
                mw.on_function_tool_call(_make_event("tool"))

    def test_past_timeout_exceeding_max_reminders_allows_through(self):
        times = [0.0] + [3700.0] * 10
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=2)
            # Exhaust reminders
            for _ in range(2):
                with pytest.raises(ModelRetry):
                    mw.on_function_tool_call(_make_event("tool"))
            # Now past max — should not raise
            mw.on_function_tool_call(_make_event("tool"))
