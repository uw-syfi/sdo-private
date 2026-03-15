"""Unit tests for soft step-limit callbacks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app_operator.pydantic_ai._soft_limit import (
    _soft_threshold,
    soft_limit_history_processor,
    soft_limit_prepare_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(step_limit: int | None, run_step: int) -> MagicMock:
    ctx = MagicMock()
    ctx.deps.config.agent.step_limit = step_limit
    ctx.run_step = run_step
    return ctx


def _make_tool_def(name: str = "tool") -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


# ---------------------------------------------------------------------------
# _soft_threshold
# ---------------------------------------------------------------------------


class TestSoftThreshold:
    def test_none_returns_none(self):
        assert _soft_threshold(None) is None

    def test_large_limit(self):
        assert _soft_threshold(50) == 45

    def test_exactly_five(self):
        assert _soft_threshold(5) == 0

    def test_less_than_five(self):
        assert _soft_threshold(3) == 0

    def test_zero(self):
        assert _soft_threshold(0) == 0


# ---------------------------------------------------------------------------
# soft_limit_history_processor
# ---------------------------------------------------------------------------


class TestSoftLimitHistoryProcessor:
    def test_no_limit_returns_messages_unchanged(self):
        ctx = _make_ctx(step_limit=None, run_step=100)
        messages = [MagicMock(), MagicMock()]
        result = soft_limit_history_processor(ctx, messages)
        assert result is messages

    def test_before_threshold_returns_messages_unchanged(self):
        ctx = _make_ctx(step_limit=50, run_step=40)  # threshold = 45
        messages = [MagicMock()]
        result = soft_limit_history_processor(ctx, messages)
        assert result is messages

    def test_at_threshold_appends_wrap_up_message(self):
        ctx = _make_ctx(step_limit=50, run_step=45)  # threshold = 45
        messages = [MagicMock()]
        result = soft_limit_history_processor(ctx, messages)
        assert len(result) == 2
        assert result[0] is messages[0]
        # The appended item should be a ModelRequest with a SystemPromptPart
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        appended = result[1]
        assert isinstance(appended, ModelRequest)
        assert len(appended.parts) == 1
        assert isinstance(appended.parts[0], SystemPromptPart)
        assert "final response" in appended.parts[0].content.lower()

    def test_after_threshold_appends_wrap_up_message(self):
        ctx = _make_ctx(step_limit=50, run_step=49)  # threshold = 45
        messages = [MagicMock()]
        result = soft_limit_history_processor(ctx, messages)
        assert len(result) == 2

    def test_original_messages_not_mutated(self):
        ctx = _make_ctx(step_limit=10, run_step=5)  # threshold = 5
        messages = [MagicMock()]
        result = soft_limit_history_processor(ctx, messages)
        assert len(messages) == 1  # original unchanged
        assert len(result) == 2

    def test_step_limit_leq_5_fires_at_step_0(self):
        ctx = _make_ctx(step_limit=3, run_step=0)  # threshold = 0
        messages = []
        result = soft_limit_history_processor(ctx, messages)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# soft_limit_prepare_tools
# ---------------------------------------------------------------------------


class TestSoftLimitPrepareTools:
    @pytest.mark.asyncio
    async def test_no_limit_returns_tools_unchanged(self):
        ctx = _make_ctx(step_limit=None, run_step=100)
        tools = [_make_tool_def()]
        result = await soft_limit_prepare_tools(ctx, tools)
        assert result is tools

    @pytest.mark.asyncio
    async def test_before_threshold_returns_tools_unchanged(self):
        ctx = _make_ctx(step_limit=50, run_step=40)
        tools = [_make_tool_def("a"), _make_tool_def("b")]
        result = await soft_limit_prepare_tools(ctx, tools)
        assert result is tools

    @pytest.mark.asyncio
    async def test_at_threshold_returns_empty_list(self):
        ctx = _make_ctx(step_limit=50, run_step=45)  # threshold = 45
        tools = [_make_tool_def("a"), _make_tool_def("b")]
        result = await soft_limit_prepare_tools(ctx, tools)
        assert result == []

    @pytest.mark.asyncio
    async def test_after_threshold_returns_empty_list(self):
        ctx = _make_ctx(step_limit=50, run_step=49)
        tools = [_make_tool_def()]
        result = await soft_limit_prepare_tools(ctx, tools)
        assert result == []

    @pytest.mark.asyncio
    async def test_step_limit_leq_5_fires_at_step_0(self):
        ctx = _make_ctx(step_limit=3, run_step=0)
        tools = [_make_tool_def()]
        result = await soft_limit_prepare_tools(ctx, tools)
        assert result == []
