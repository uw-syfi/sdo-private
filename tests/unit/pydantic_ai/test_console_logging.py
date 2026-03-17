"""Tests for ConsoleLoggingMiddleware."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from app_operator.pydantic_ai._console_logging import (
    ConsoleLoggingMiddleware,
    _fmt_args,
    _fmt_k,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Minimal stub that satisfies the _agent.agent_name access pattern."""

    def __init__(self, name: str = "Code Analyzer"):
        self._name = name

    @property
    def agent_name(self) -> str:
        return self._name


class _FakeResult:
    def __init__(self, output: Any):
        self.output = output


def _make_recorder(input_tokens: int | None = 0) -> Any:
    recorder = MagicMock()
    recorder.total_usage.input_tokens = input_tokens
    return recorder


def _make_mw(
    context_window: int = 200_000,
    input_tokens: int | None = 0,
    agent_name: str = "Code Analyzer",
    logger: logging.Logger | None = None,
) -> ConsoleLoggingMiddleware:
    """Create a ConsoleLoggingMiddleware with a fake agent attached."""
    mw = ConsoleLoggingMiddleware(
        context_window=context_window,
        recorder=_make_recorder(input_tokens),
        logger=logger,
    )
    mw.on_attach(_FakeAgent(agent_name))  # type: ignore[arg-type]
    return mw


def _capture_logs(logger_name: str) -> tuple[list[str], logging.Handler]:
    """Return (records, handler) — records is a mutable list populated by the handler."""
    records: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = _Sink()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger(logger_name).addHandler(handler)
    logging.getLogger(logger_name).setLevel(logging.DEBUG)
    return records, handler


def _make_tool_call_event(tool_name: str, args: dict | str | None = None) -> Any:
    event = MagicMock()
    event.part.tool_name = tool_name
    event.part.args = args
    return event


# ---------------------------------------------------------------------------
# _fmt_k tests
# ---------------------------------------------------------------------------


def test_fmt_k_rounds_to_nearest_k():
    assert _fmt_k(23_000) == "23k"
    assert _fmt_k(200_000) == "200k"
    assert _fmt_k(0) == "0k"


def test_fmt_k_none_returns_question_mark():
    assert _fmt_k(None) == "?k"


# ---------------------------------------------------------------------------
# _fmt_args tests
# ---------------------------------------------------------------------------


def test_fmt_args_elides_content_field():
    args = {"content": "x" * 200}
    result = _fmt_args(args)
    assert "content=<200 chars>" in result
    assert "x" not in result


def test_fmt_args_elides_new_str_field():
    args = {"new_str": "y" * 50}
    result = _fmt_args(args)
    assert "new_str=<50 chars>" in result


def test_fmt_args_truncates_long_regular_field():
    args = {"path": "a" * 200}
    result = _fmt_args(args)
    assert len(result) < 200


def test_fmt_args_short_field_shown_verbatim():
    args = {"path": "/tmp/foo"}
    result = _fmt_args(args)
    assert "path='/tmp/foo'" in result


def test_fmt_args_none_returns_empty():
    assert _fmt_args(None) == ""


def test_fmt_args_string_truncated():
    long_str = "x" * 200
    result = _fmt_args(long_str)
    assert result.endswith("\u2026")
    assert len(result) <= 122  # 120 + ellipsis


def test_fmt_args_short_string_verbatim():
    assert _fmt_args("short") == "short"


# ---------------------------------------------------------------------------
# ConsoleLoggingMiddleware hook tests
# ---------------------------------------------------------------------------

_LOGGER_NAME = "app_operator.pydantic_ai._console_logging"


def test_on_function_tool_call_logs_usage_prefix_and_tool():
    records, handler = _capture_logs(_LOGGER_NAME)
    try:
        mw = _make_mw(context_window=200_000, input_tokens=0)
        mw.on_function_tool_call(_make_tool_call_event("read_file", {"path": "/tmp/x"}))
        combined = " ".join(records)
        assert "Code Analyzer" in combined
        assert "0k/200k" in combined
        assert "read_file" in combined
    finally:
        logging.getLogger(_LOGGER_NAME).removeHandler(handler)


def test_on_function_tool_call_shows_updated_token_count():
    records, handler = _capture_logs(_LOGGER_NAME)
    try:
        mw = _make_mw(context_window=200_000, input_tokens=23_000)
        mw.on_function_tool_call(_make_tool_call_event("write_file"))
        assert any("23k/200k" in r for r in records)
    finally:
        logging.getLogger(_LOGGER_NAME).removeHandler(handler)


def test_on_part_end_logs_thinking_with_usage_prefix():
    from pydantic_ai.messages import ThinkingPart

    records, handler = _capture_logs(_LOGGER_NAME)
    try:
        mw = _make_mw(context_window=100_000, input_tokens=5_000)
        part = ThinkingPart(content="some thoughts")
        event = MagicMock()
        event.part = part
        mw.on_part_end(event)
        combined = " ".join(records)
        assert "5k/100k" in combined
        assert "<thinking>" in combined
        assert "some thoughts" in combined
    finally:
        logging.getLogger(_LOGGER_NAME).removeHandler(handler)


def test_after_run_logs_agent_name_and_output_no_token_prefix():
    records, handler = _capture_logs(_LOGGER_NAME)
    try:
        mw = _make_mw()
        mw.after_run(_FakeResult("Analysis complete."))
        combined = " ".join(records)
        assert "Code Analyzer" in combined
        assert "Analysis complete" in combined
        # after_run does NOT include usage prefix
        assert "|" not in combined
    finally:
        logging.getLogger(_LOGGER_NAME).removeHandler(handler)
