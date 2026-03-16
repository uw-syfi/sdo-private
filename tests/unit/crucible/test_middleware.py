"""Unit tests for sregym_agents.crucible.middleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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

    def test_two_identical_calls_do_not_nudge(self):
        mw = LoopDetectionMiddleware()
        self._call(mw, 2)
        assert mw._pending_nudge is None

    def test_three_identical_calls_sets_nudge(self):
        mw = LoopDetectionMiddleware()
        self._call(mw, 3)
        assert mw._pending_nudge is not None

    def test_three_different_calls_do_not_nudge(self):
        mw = LoopDetectionMiddleware()
        mw.on_function_tool_call(_make_event("tool1", {"a": "1"}))
        mw.on_function_tool_call(_make_event("tool2", {"a": "2"}))
        mw.on_function_tool_call(_make_event("tool3", {"a": "3"}))
        assert mw._pending_nudge is None

    def test_after_max_reminders_no_nudge(self):
        mw = LoopDetectionMiddleware(max_loop_reminders=1)
        # First triple sets nudge (reminder 1/1)
        self._call(mw, 3)
        assert mw._pending_nudge is not None
        mw._pending_nudge = None  # consume it
        # Now at max_loop_reminders; next identical call should not nudge
        mw.on_function_tool_call(_make_event("t", {"cmd": "ls"}))
        assert mw._pending_nudge is None

    def test_after_run_clears_history(self):
        mw = LoopDetectionMiddleware()
        # Call twice
        self._call(mw, 2)
        # Clear state
        mw.after_run(None)
        # Now three identical calls should nudge again (deque was cleared)
        self._call(mw, 3)
        assert mw._pending_nudge is not None

    def test_nudge_injected_into_messages(self):
        mw = LoopDetectionMiddleware()
        self._call(mw, 3)
        messages = mw.before_model_req_edit_messages(None, [])
        assert len(messages) == 1
        assert mw._pending_nudge is None  # consumed

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
    def test_within_timeout_no_nudge(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 5.0]):
            mw = TimeoutMiddleware(timeout_seconds=60)
            mw.on_function_tool_call(_make_event("tool"))
        assert mw._pending_nudge is None

    def test_past_timeout_first_call_sets_nudge(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            mw.on_function_tool_call(_make_event("tool"))
        assert mw._pending_nudge is not None

    def test_nudge_injected_into_messages(self):
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            mw.on_function_tool_call(_make_event("tool"))
        messages = mw.before_model_req_edit_messages(None, [])
        assert len(messages) == 1
        assert mw._pending_nudge is None  # consumed

    def test_past_timeout_at_max_reminders_still_nudges(self):
        times = [0.0] + [3700.0] * 3
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=3)
            for _ in range(3):
                mw.on_function_tool_call(_make_event("tool"))
                assert mw._pending_nudge is not None
                mw._pending_nudge = None  # consume each nudge

    def test_past_timeout_exceeding_max_reminders_no_nudge(self):
        times = [0.0] + [3700.0] * 10
        with patch("sregym_agents.crucible.middleware.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=2)
            # Exhaust reminders
            for _ in range(2):
                mw.on_function_tool_call(_make_event("tool"))
                mw._pending_nudge = None  # consume each nudge
            # Now past max — should not nudge
            mw.on_function_tool_call(_make_event("tool"))
            assert mw._pending_nudge is None
