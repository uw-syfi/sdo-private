"""Unit tests for SoftLimitExtension middleware."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from libs.agent_mw._soft_limit import SoftLimitExtension, _soft_threshold

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(run_step: int) -> MagicMock:
    ctx = MagicMock()
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
# SoftLimitExtension.on_attach
# ---------------------------------------------------------------------------


class TestOnAttach:
    def test_sets_usage_limits_when_step_limit_provided(self):
        ext = SoftLimitExtension(step_limit=100)
        agent = MagicMock()
        ext.on_attach(agent)
        from pydantic_ai.usage import UsageLimits

        assert agent._usage_limits == UsageLimits(request_limit=100)

    def test_no_usage_limits_when_step_limit_is_none(self):
        ext = SoftLimitExtension(step_limit=None)
        agent = MagicMock()
        agent._usage_limits = "original"
        ext.on_attach(agent)
        assert agent._usage_limits == "original"


# ---------------------------------------------------------------------------
# SoftLimitExtension.before_model_req_edit_messages
# ---------------------------------------------------------------------------


class TestBeforeModelReqEditMessages:
    def test_no_limit_returns_messages_unchanged(self):
        ext = SoftLimitExtension(step_limit=None)
        ctx = _make_ctx(run_step=100)
        messages = [MagicMock(), MagicMock()]
        result = ext.before_model_req_edit_messages(ctx, messages)  # type: ignore[arg-type]
        assert result is messages

    def test_before_threshold_returns_messages_unchanged(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=40)  # threshold = 45
        messages = [MagicMock()]
        result = ext.before_model_req_edit_messages(ctx, messages)  # type: ignore[arg-type]
        assert result is messages

    def test_at_threshold_appends_wrap_up_message(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=45)  # threshold = 45
        messages = [MagicMock()]
        result = ext.before_model_req_edit_messages(ctx, messages)  # type: ignore[arg-type]
        assert len(result) == 2
        assert result[0] is messages[0]
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        appended = result[1]
        assert isinstance(appended, ModelRequest)
        assert len(appended.parts) == 1
        assert isinstance(appended.parts[0], SystemPromptPart)
        assert "final response" in appended.parts[0].content.lower()

    def test_after_threshold_appends_wrap_up_message(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=49)  # threshold = 45
        messages = [MagicMock()]
        result = ext.before_model_req_edit_messages(ctx, messages)  # type: ignore[arg-type]
        assert len(result) == 2

    def test_original_messages_not_mutated(self):
        ext = SoftLimitExtension(step_limit=10)
        ctx = _make_ctx(run_step=5)  # threshold = 5
        messages = [MagicMock()]
        result = ext.before_model_req_edit_messages(ctx, messages)  # type: ignore[arg-type]
        assert len(result) == 2

    def test_step_limit_leq_5_fires_at_step_0(self):
        ext = SoftLimitExtension(step_limit=3)
        ctx = _make_ctx(run_step=0)  # threshold = 0
        messages = []
        result = ext.before_model_req_edit_messages(ctx, messages)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# SoftLimitExtension.before_model_req_edit_tools
# ---------------------------------------------------------------------------


class TestBeforeModelReqEditTools:
    def test_no_limit_returns_tools_unchanged(self):
        ext = SoftLimitExtension(step_limit=None)
        ctx = _make_ctx(run_step=100)
        tools = [_make_tool_def()]
        result = asyncio.run(ext.before_model_req_edit_tools(ctx, tools))  # type: ignore[arg-type]
        assert result is tools

    def test_before_threshold_returns_tools_unchanged(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=40)
        tools = [_make_tool_def("a"), _make_tool_def("b")]
        result = asyncio.run(ext.before_model_req_edit_tools(ctx, tools))  # type: ignore[arg-type]
        assert result is tools

    def test_at_threshold_returns_empty_list(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=45)  # threshold = 45
        tools = [_make_tool_def("a"), _make_tool_def("b")]
        result = asyncio.run(ext.before_model_req_edit_tools(ctx, tools))  # type: ignore[arg-type]
        assert result == []

    def test_after_threshold_returns_empty_list(self):
        ext = SoftLimitExtension(step_limit=50)
        ctx = _make_ctx(run_step=49)
        tools = [_make_tool_def()]
        result = asyncio.run(ext.before_model_req_edit_tools(ctx, tools))  # type: ignore[arg-type]
        assert result == []

    def test_step_limit_leq_5_fires_at_step_0(self):
        ext = SoftLimitExtension(step_limit=3)
        ctx = _make_ctx(run_step=0)
        tools = [_make_tool_def()]
        result = asyncio.run(ext.before_model_req_edit_tools(ctx, tools))  # type: ignore[arg-type]
        assert result == []
