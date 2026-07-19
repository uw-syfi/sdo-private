# pyright: reportPrivateUsage=false
"""Unit tests for benchmarks.sregym.agents.crucible.middleware."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from libs.agent_mw import (
    LoopDetectionMiddleware,
    StallDetectionMiddleware,
    ThinkingRepetitionMiddleware,
    TimeoutMiddleware,
)


def _make_event(tool_name: str, args: dict[str, Any] | None = None) -> MagicMock:
    event = MagicMock()
    event.part.tool_name = tool_name
    event.part.args = args if args is not None else {"cmd": "ls"}
    return event


def _make_thinking_event(content: str) -> MagicMock:
    """Create a mock PartEndEvent wrapping a ThinkingPart."""
    from pydantic_ai.messages import ThinkingPart

    event = MagicMock()
    part = ThinkingPart(content=content)
    event.part = part
    return event


def _make_ctx(run_step: int = 1) -> MagicMock:
    ctx = MagicMock()
    ctx.run_step = run_step
    return ctx


# ---------------------------------------------------------------------------
# LoopDetectionMiddleware
# ---------------------------------------------------------------------------


class TestLoopDetectionMiddleware:
    def _call(
        self,
        mw: LoopDetectionMiddleware,
        n: int = 1,
        tool: str = "t",
        args: dict[str, Any] | None = None,
    ) -> None:
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
# ThinkingRepetitionMiddleware
# ---------------------------------------------------------------------------


class TestThinkingRepetitionMiddleware:
    def test_diverse_thinking_no_nudge(self):
        mw = ThinkingRepetitionMiddleware()
        for i in range(5):
            mw.on_part_end(_make_thinking_event(f"unique thinking block {i}"))
        assert mw._pending_nudge is None
        assert not mw._force_submit

    def test_repeated_thinking_sets_nudge(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3)
        for _ in range(3):
            mw.on_part_end(_make_thinking_event("Developing ConfigMap Parser" + " " * 200))
        assert mw._pending_nudge is not None
        assert mw._nudge_count == 1

    def test_nudge_consumed_by_edit_messages(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3)
        for _ in range(3):
            mw.on_part_end(_make_thinking_event("same thinking"))
        messages = mw.before_model_req_edit_messages(None, [])
        assert len(messages) == 1
        assert mw._pending_nudge is None

    def test_force_submit_after_max_nudges(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3, max_nudges=2)
        # First batch: nudge 1
        for _ in range(3):
            mw.on_part_end(_make_thinking_event("same thinking"))
        assert mw._nudge_count == 1
        mw._pending_nudge = None  # consume
        # Second batch: nudge 2
        mw.on_part_end(_make_thinking_event("same thinking"))
        assert mw._nudge_count == 2
        mw._pending_nudge = None  # consume
        # Third batch: force submit
        mw.on_part_end(_make_thinking_event("same thinking"))
        assert mw._force_submit is True

    def test_force_submit_strips_tools(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3, max_nudges=1)
        for _ in range(3):
            mw.on_part_end(_make_thinking_event("same thinking"))
        mw._pending_nudge = None
        mw.on_part_end(_make_thinking_event("same thinking"))
        assert mw._force_submit is True
        result = asyncio.run(
            mw.before_model_req_edit_tools(None, [{"name": "exec_bash"}])  # type: ignore[arg-type]
        )
        assert result == []

    def test_no_force_submit_returns_tools_unchanged(self):
        mw = ThinkingRepetitionMiddleware()
        tools = [{"name": "exec_bash"}]
        result = asyncio.run(
            mw.before_model_req_edit_tools(None, tools)  # type: ignore[arg-type]
        )
        assert result == tools

    def test_after_run_resets_state(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3)
        for _ in range(3):
            mw.on_part_end(_make_thinking_event("same thinking"))
        assert mw._nudge_count == 1
        mw.after_run(None)
        assert mw._nudge_count == 0
        assert len(mw._recent_hashes) == 0
        assert not mw._force_submit

    def test_non_thinking_parts_ignored(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3)
        # Create a non-ThinkingPart event
        event = MagicMock()
        event.part = MagicMock()  # not a ThinkingPart
        for _ in range(5):
            mw.on_part_end(event)
        assert mw._pending_nudge is None
        assert len(mw._recent_hashes) == 0

    def test_empty_thinking_parts_ignored(self):
        mw = ThinkingRepetitionMiddleware(window_size=5, duplicate_threshold=3)
        from pydantic_ai.messages import ThinkingPart

        event = MagicMock()
        event.part = ThinkingPart(content="")
        for _ in range(5):
            mw.on_part_end(event)
        # Empty content passes has_content() check — hashes should still be stored
        # but this verifies no crash occurs


# ---------------------------------------------------------------------------
# StallDetectionMiddleware
# ---------------------------------------------------------------------------


class TestStallDetectionMiddleware:
    def test_no_trigger_within_time_gate(self):
        """No stall detection before min_elapsed_seconds."""
        # Start at t=0, still at t=60 (< 120s gate)
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [60.0] * 10):
            mw = StallDetectionMiddleware(max_no_tool_requests=2, min_elapsed_seconds=120)
            mw.before_run()
            # Multiple no-tool requests within time gate
            for step in range(2, 8):
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._pending_nudge is None
        assert mw._consecutive_no_tool == 0  # not incremented within gate

    def test_no_trigger_with_tool_calls(self):
        """Tool calls reset the counter."""
        # Past time gate
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 20):
            mw = StallDetectionMiddleware(max_no_tool_requests=3, min_elapsed_seconds=120)
            mw.before_run()
            for step in range(2, 10):
                mw.on_function_tool_call(_make_event("exec_bash"))
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._consecutive_no_tool == 0
        assert mw._nudge_count == 0

    def test_no_trigger_below_threshold(self):
        """Fewer than max_no_tool_requests doesn't trigger."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 10):
            mw = StallDetectionMiddleware(max_no_tool_requests=5, min_elapsed_seconds=120)
            mw.before_run()
            # 4 no-tool requests (below threshold of 5)
            for step in range(2, 6):
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._consecutive_no_tool == 4
        assert mw._nudge_count == 0

    def test_trigger_after_threshold(self):
        """5 consecutive no-tool requests past time gate triggers nudge."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 20):
            mw = StallDetectionMiddleware(max_no_tool_requests=5, min_elapsed_seconds=120)
            mw.before_run()
            for step in range(2, 7):  # steps 2-6: exactly 5 no-tool requests
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._nudge_count == 1

    def test_nudge_injected_into_messages(self):
        """Nudge is appended to messages."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 20):
            mw = StallDetectionMiddleware(max_no_tool_requests=2, min_elapsed_seconds=0)
            mw.before_run()
            mw.before_model_req_edit_messages(_make_ctx(run_step=2), [])
            messages = mw.before_model_req_edit_messages(_make_ctx(run_step=3), [])
        assert len(messages) == 1  # nudge message appended

    def test_force_submit_after_max_nudges(self):
        """After max_nudges, tools are stripped."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 50):
            mw = StallDetectionMiddleware(max_no_tool_requests=2, min_elapsed_seconds=0, max_nudges=2)
            mw.before_run()
            # Generate enough no-tool requests to exhaust nudges
            for step in range(2, 20):
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._force_submit is True
        result = asyncio.run(
            mw.before_model_req_edit_tools(None, [{"name": "exec_bash"}])  # type: ignore[arg-type]
        )
        assert result == []

    def test_tool_call_resets_counter_past_gate(self):
        """A tool call resets the consecutive counter even past the time gate."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 20):
            mw = StallDetectionMiddleware(max_no_tool_requests=5, min_elapsed_seconds=120)
            mw.before_run()
            # 3 no-tool requests
            for step in range(2, 5):
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
            assert mw._consecutive_no_tool == 3
            # Tool call + next request resets
            mw.on_function_tool_call(_make_event("exec_bash"))
            mw.before_model_req_edit_messages(_make_ctx(run_step=5), [])
            assert mw._consecutive_no_tool == 0

    def test_first_request_skipped(self):
        """run_step=1 is skipped (no previous request to evaluate)."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0, 200.0]):
            mw = StallDetectionMiddleware(max_no_tool_requests=1, min_elapsed_seconds=0)
            mw.before_run()
            messages = mw.before_model_req_edit_messages(_make_ctx(run_step=1), [])
        assert messages == []
        assert mw._consecutive_no_tool == 0

    def test_after_run_resets(self):
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0] + [200.0] * 10):
            mw = StallDetectionMiddleware(max_no_tool_requests=2, min_elapsed_seconds=0)
            mw.before_run()
            for step in range(2, 6):
                mw.before_model_req_edit_messages(_make_ctx(run_step=step), [])
        assert mw._nudge_count > 0
        mw.after_run(None)
        assert mw._consecutive_no_tool == 0
        assert mw._nudge_count == 0
        assert not mw._force_submit


# ---------------------------------------------------------------------------
# TimeoutMiddleware
# ---------------------------------------------------------------------------


class TestTimeoutMiddleware:
    def test_within_timeout_no_nudge(self):
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0, 5.0]):
            mw = TimeoutMiddleware(timeout_seconds=60)
            messages = mw.before_model_req_edit_messages(_make_ctx(), [])
        assert messages == []

    def test_past_timeout_sets_nudge(self):
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            messages = mw.before_model_req_edit_messages(_make_ctx(), [])
        assert len(messages) == 1
        assert mw._reminders == 1

    def test_nudge_text_includes_elapsed_minutes(self):
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            messages = mw.before_model_req_edit_messages(_make_ctx(), [])
        nudge_text = str(messages[0].parts[0].content)  # type: ignore[union-attr]
        assert "61 minutes" in nudge_text

    def test_max_reminders_then_force_submit(self):
        times = [0.0] + [3700.0] * 10
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=2)
            # Exhaust reminders
            for _ in range(2):
                mw.before_model_req_edit_messages(_make_ctx(), [])
            assert mw._reminders == 2
            assert not mw._force_submit
            # Next request: force submit
            mw.before_model_req_edit_messages(_make_ctx(), [])
            assert mw._force_submit is True

    def test_force_submit_strips_tools(self):
        times = [0.0] + [3700.0] * 10
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=1)
            mw.before_model_req_edit_messages(_make_ctx(), [])  # reminder 1
            mw.before_model_req_edit_messages(_make_ctx(), [])  # force submit
        assert mw._force_submit is True
        result = asyncio.run(
            mw.before_model_req_edit_tools(None, [{"name": "exec_bash"}])  # type: ignore[arg-type]
        )
        assert result == []

    def test_no_force_submit_returns_tools_unchanged(self):
        mw = TimeoutMiddleware(timeout_seconds=3600)
        tools = [{"name": "exec_bash"}]
        result = asyncio.run(
            mw.before_model_req_edit_tools(None, tools)  # type: ignore[arg-type]
        )
        assert result == tools

    def test_fires_without_tool_calls(self):
        """Timeout now fires on model requests, not tool calls."""
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=[0.0, 3700.0]):
            mw = TimeoutMiddleware(timeout_seconds=3600)
            # No tool calls — just a model request
            messages = mw.before_model_req_edit_messages(_make_ctx(), [])
        assert len(messages) == 1  # nudge appended

    def test_before_run_resets_state(self):
        """before_run() resets start_time so reused instances measure from run start."""
        # Simulate a first run that exhausted reminders and set force_submit.
        times = [0.0] + [3700.0] * 10
        with patch("libs.agent_mw._behavior_guards.time.monotonic", side_effect=times):
            mw = TimeoutMiddleware(timeout_seconds=3600, max_timeout_reminders=1)
            mw.before_model_req_edit_messages(_make_ctx(), [])  # reminder 1
            mw.before_model_req_edit_messages(_make_ctx(), [])  # force_submit = True
        assert mw._force_submit is True
        assert mw._reminders == 1

        # before_run() should reset everything for the next run.
        with patch("libs.agent_mw._behavior_guards.time.monotonic", return_value=9999.0):
            mw.before_run()
        assert mw._force_submit is False
        assert mw._reminders == 0
        # A subsequent request well within timeout should add no nudge.
        with patch("libs.agent_mw._behavior_guards.time.monotonic", return_value=10001.0):
            messages = mw.before_model_req_edit_messages(_make_ctx(), [])
        assert messages == []
