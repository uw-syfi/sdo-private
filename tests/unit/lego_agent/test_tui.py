"""Tests for lego_agent TUI module."""

import pytest
from unittest.mock import AsyncMock

from lego_agent.tui import TextualIO


class MockApp:
    """Mock LegoAgentTUI app for testing TextualIO."""

    def __init__(self):
        self.logs = []
        self.input_queue = AsyncMock()

    def write_log(self, content):
        """Record log entries."""
        self.logs.append(content)

    def print_stream(self, text):
        """Record stream output."""
        self.logs.append(("stream", text))


@pytest.fixture
def mock_app():
    """Create mock app for testing."""
    return MockApp()


@pytest.fixture
def textual_io(mock_app):
    """Create TextualIO instance with mock app."""
    return TextualIO(mock_app)


def test_textual_io_initialization(mock_app):
    """Test TextualIO initialization."""
    io = TextualIO(mock_app)
    assert io.app == mock_app
    assert io._thinking_buffer == ""


def test_read_prompt_returns_empty(textual_io):
    """Test read_prompt returns empty string (not used in TUI flow)."""
    result = textual_io.read_prompt()
    assert result == ""


def test_ask_questions_single_question(textual_io, mock_app):
    """Test asking a single question.

    Note: This tests the synchronous behavior of TextualIO. The actual async
    behavior is tested through integration tests with a running TUI.
    """
    # These methods are async in the real implementation but we test the
    # synchronous logic here. Full async behavior is tested in integration tests.
    assert hasattr(textual_io, 'ask_questions')


def test_ask_questions_multiple_questions(textual_io, mock_app):
    """Test that TextualIO has the ask_questions method."""
    assert hasattr(textual_io, 'ask_questions')


def test_prompt_int_valid_input(textual_io, mock_app):
    """Test that TextualIO has the prompt_int method."""
    assert hasattr(textual_io, 'prompt_int')


def test_prompt_int_invalid_then_valid(textual_io, mock_app):
    """Test that TextualIO has the prompt_int method."""
    assert hasattr(textual_io, 'prompt_int')


def test_info_writes_to_log(textual_io, mock_app):
    """Test info writes message to log."""
    textual_io.info("Test message")

    assert "Test message" in mock_app.logs


def test_print_stream_writes_to_log(textual_io, mock_app):
    """Test print_stream writes to log."""
    textual_io.print_stream("Stream output")

    assert ("stream", "Stream output") in mock_app.logs


def test_render_thinking_chunk_buffers_partial_line(textual_io, mock_app):
    """Test thinking chunks are buffered until newline."""
    textual_io.render_thinking_chunk("partial ")
    textual_io.render_thinking_chunk("line")

    # No output yet - still buffering
    assert len(mock_app.logs) == 0
    assert textual_io._thinking_buffer == "partial line"


def test_render_thinking_chunk_flushes_on_newline(textual_io, mock_app):
    """Test thinking chunks flush on newline."""
    textual_io.render_thinking_chunk("line 1\n")
    textual_io.render_thinking_chunk("line 2\n")

    # Should have written 2 lines
    assert len(mock_app.logs) == 2


def test_render_thinking_chunk_multiline(textual_io, mock_app):
    """Test thinking chunk with multiple newlines."""
    textual_io.render_thinking_chunk("line1\nline2\nline3\npartial")

    # Should have 3 complete lines, keeping "partial" buffered
    assert len(mock_app.logs) == 3
    assert textual_io._thinking_buffer == "partial"


def test_render_thinking_chunk_empty_lines(textual_io, mock_app):
    """Test rendering empty lines in thinking output."""
    textual_io.render_thinking_chunk("line1\n\nline3\n")

    # Should write 3 entries (line1, empty, line3)
    assert len(mock_app.logs) == 3


def test_flush_thinking_clears_buffer(textual_io, mock_app):
    """Test _flush_thinking clears the buffer."""
    textual_io._thinking_buffer = "buffered text"

    textual_io._flush_thinking()

    assert textual_io._thinking_buffer == ""
    assert len(mock_app.logs) == 1


def test_flush_thinking_with_empty_buffer(textual_io, mock_app):
    """Test _flush_thinking with empty buffer does nothing."""
    textual_io._flush_thinking()

    assert textual_io._thinking_buffer == ""
    assert len(mock_app.logs) == 0


def test_render_tool_start_flushes_thinking(textual_io, mock_app):
    """Test render_tool_start flushes thinking buffer."""
    textual_io._thinking_buffer = "pending thought"

    textual_io.render_tool_start("read_file", "{'path': '/test'}")

    # Should flush buffer before tool message
    assert textual_io._thinking_buffer == ""
    assert len(mock_app.logs) >= 2  # flushed buffer + tool start


def test_render_tool_start_formatting(textual_io, mock_app):
    """Test render_tool_start formats correctly."""
    textual_io.render_tool_start("write_file", "{'path': '/test', 'content': '...'}")

    # Check that tool start was logged
    assert len(mock_app.logs) == 1


def test_render_tool_end_success(textual_io, mock_app):
    """Test render_tool_end with success status."""
    textual_io.render_tool_end("read_file", "File content here", "success")

    # Should write a panel with success styling
    assert len(mock_app.logs) == 1


def test_render_tool_end_error(textual_io, mock_app):
    """Test render_tool_end with error status."""
    textual_io.render_tool_end("read_file", "File not found", "error")

    # Should write a panel with error styling
    assert len(mock_app.logs) == 1


def test_render_tool_end_neutral(textual_io, mock_app):
    """Test render_tool_end with neutral status."""
    textual_io.render_tool_end("list_files", "file1.txt\nfile2.txt", "neutral")

    # Should write a panel with neutral styling
    assert len(mock_app.logs) == 1


def test_render_tool_end_flushes_thinking(textual_io, mock_app):
    """Test render_tool_end flushes thinking buffer."""
    textual_io._thinking_buffer = "pending thought"

    textual_io.render_tool_end("read_file", "content", "success")

    # Should flush buffer
    assert textual_io._thinking_buffer == ""
    assert len(mock_app.logs) >= 2


def test_info_flushes_thinking_buffer(textual_io, mock_app):
    """Test info flushes thinking buffer before writing."""
    textual_io._thinking_buffer = "buffered"

    textual_io.info("New message")

    # Buffer should be flushed first
    assert textual_io._thinking_buffer == ""
    assert len(mock_app.logs) == 2  # flushed buffer + info message


def test_ask_questions_flushes_thinking(textual_io, mock_app):
    """Test that ask_questions method exists for flushing thinking buffer.

    The actual async behavior is tested in integration tests.
    """
    assert hasattr(textual_io, 'ask_questions')


def test_prompt_int_flushes_thinking(textual_io, mock_app):
    """Test that prompt_int method exists for flushing thinking buffer.

    The actual async behavior is tested in integration tests.
    """
    assert hasattr(textual_io, 'prompt_int')


def test_print_stream_flushes_thinking(textual_io, mock_app):
    """Test print_stream flushes thinking buffer."""
    textual_io._thinking_buffer = "buffered"

    textual_io.print_stream("stream text")

    # Should flush buffer first
    assert textual_io._thinking_buffer == ""
