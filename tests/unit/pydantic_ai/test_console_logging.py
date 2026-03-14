"""Tests for ConsoleLoggingMiddleware."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    # truncated at 120 chars + repr
    assert len(result) < 200


def test_fmt_args_short_field_shown_verbatim():
    args = {"path": "/tmp/foo"}
    result = _fmt_args(args)
    assert "path='/tmp/foo'" in result


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


def test_before_tool_call_logs_agent_name_and_tool():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.before_tool_call("read_file", {"path": "/tmp/x"})
        assert any("[Code Analyzer]" in r and "read_file" in r for r in records)
    finally:
        logger.remove(sink_id)


def test_after_tool_call_logs_agent_name_and_result():
    records, sink_id = _capture_logs()
    try:
        mw = _attach(ConsoleLoggingMiddleware())
        mw.after_tool_call("read_file", {"path": "/tmp/x"}, "file contents")
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


def test_before_tool_call_returns_true():
    mw = _attach(ConsoleLoggingMiddleware())
    result = mw.before_tool_call("any_tool", {})
    assert result is True
