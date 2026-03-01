"""Tests for lego_agent.streaming shared utilities."""

from types import SimpleNamespace
from lego_agent.streaming import parse_chunk_content, extract_tool_result


# --- parse_chunk_content ---


class TestParseChunkContent:
    def test_plain_string(self):
        assert parse_chunk_content("hello") == "hello"

    def test_list_of_text_dicts(self):
        content = [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]
        assert parse_chunk_content(content) == "hello world"

    def test_list_of_thinking_dicts(self):
        content = [{"type": "thinking", "thinking": "hmm"}]
        assert parse_chunk_content(content) == "hmm"

    def test_list_of_strings(self):
        assert parse_chunk_content(["a", "b"]) == "ab"

    def test_mixed_list(self):
        content = [
            {"type": "text", "text": "hi"},
            "there",
            {"type": "thinking", "thinking": "..."},
        ]
        assert parse_chunk_content(content) == "hithere..."

    def test_empty_list(self):
        assert parse_chunk_content([]) == ""

    def test_non_string_non_list(self):
        assert parse_chunk_content(42) == "42"


# --- extract_tool_result ---


class TestExtractToolResult:
    def test_json_success(self):
        status, text = extract_tool_result(
            '{"status": "success", "output": "ok"}')
        assert status == "success"
        assert text == "ok"

    def test_json_error(self):
        status, text = extract_tool_result(
            '{"status": "error", "output": "fail"}')
        assert status == "error"
        assert text == "fail"

    def test_plain_string(self):
        status, text = extract_tool_result("just text")
        assert status == "unknown"
        assert text == "just text"

    def test_dict_content(self):
        status, text = extract_tool_result(
            {"status": "success", "output": "done"})
        assert status == "success"
        assert text == "done"

    def test_tool_message_with_content_attr(self):
        msg = SimpleNamespace(content='{"status": "error", "output": "bad"}')
        status, text = extract_tool_result(msg)
        assert status == "error"
        assert text == "bad"

    def test_truncation(self):
        long_text = "x" * 600
        _, text = extract_tool_result(long_text, max_length=100)
        assert len(text) < 200
        assert "truncated" in text

    def test_non_string_non_dict(self):
        status, text = extract_tool_result(12345)
        assert status == "unknown"
        assert text == "12345"
