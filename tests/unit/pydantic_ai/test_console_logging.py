"""Tests for ConsoleLoggingMiddleware."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from loguru import logger

from app_operator.pydantic_ai._console_logging import (
    ConsoleLoggingMiddleware,
    _fmt_args,
    _fmt_result,
)

if TYPE_CHECKING:
    from libs.pydantic_agent import AgentMiddleware

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


def _attach(mw: AgentMiddleware, agent_name: str = "Code Analyzer") -> ConsoleLoggingMiddleware:
    """Attach a fake agent to the middleware and return it (for chaining)."""
    mw.on_attach(_FakeAgent(agent_name))  # type: ignore[arg-type]
    return mw  # type: ignore[return-value]


def _capture_logs() -> tuple[list[str], Any]:
    """Return (records, sink_id) — records is a mutable list populated by the sink."""
    records: list[str] = []

    def sink(message):
        records.append(str(message))

    sink_id = logger.add(sink, format="{message}", level="DEBUG")
    return records, sink_id


def _make_tool_call_event(tool_name: str, args: dict | str | None = None) -> Any:
    event = MagicMock()
    event.part.tool_name = tool_name
    event.part.args = args
    return event


def _make_tool_result_event(tool_name: str, content: Any = "ok") -> Any:
    event = MagicMock()
    event.result.tool_name = tool_name
    event.result.content = content
    return event


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
# _fmt_result tests
# ---------------------------------------------------------------------------


def test_fmt_result_dict_shows_returncode_and_stdout():
    result = _fmt_result({"returncode": 0, "stdout": "hello"})
    assert "rc=0" in result
    assert "hello" in result


def test_fmt_result_truncates_long_string():
    long_str = "x" * 400
    result = _fmt_result(long_str)
    assert result.endswith("\u2026")
    assert len(result) <= 302  # 300 chars + ellipsis


def test_fmt_result_none():
    assert _fmt_result(None) == "<none>"


def test_fmt_result_short_string():
    result = _fmt_result("ok")
    assert result == "ok"


# ---------------------------------------------------------------------------
# ConsoleLoggingMiddleware hook tests
# ---------------------------------------------------------------------------


def test_on_function_tool_call_logs_agent_name_and_tool():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.on_function_tool_call(_make_tool_call_event("read_file", {"path": "/tmp/x"}))
        assert any("[Code Analyzer]" in r and "read_file" in r for r in records)
    finally:
        logger.remove(sink_id)


def test_on_function_tool_result_logs_agent_name_and_result():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.on_function_tool_result(_make_tool_result_event("read_file", "file contents"))
        assert any("[Code Analyzer]" in r and "read_file" in r for r in records)
    finally:
        logger.remove(sink_id)


def test_after_run_logs_agent_name_and_output():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.after_run(_FakeResult("Analysis complete."))
        assert any("[Code Analyzer]" in r and "Analysis complete" in r for r in records)
    finally:
        logger.remove(sink_id)


def test_after_run_truncates_long_output():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.after_run(_FakeResult("z" * 600))
        combined = " ".join(records)
        assert "\u2026" in combined
    finally:
        logger.remove(sink_id)
