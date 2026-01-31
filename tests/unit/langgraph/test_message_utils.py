"""Tests for message_utils module."""

from app_operator.langgraph.message_utils import extract_text


class TestExtractText:
    """Test extract_text() function."""

    def test_extract_text_from_string(self):
        """Test extracting text from a plain string."""
        content = "Hello, world!"
        result = extract_text(content)
        assert result == "Hello, world!"

    def test_extract_text_from_empty_string(self):
        """Test extracting text from an empty string."""
        content = ""
        result = extract_text(content)
        assert result == ""

    def test_extract_text_from_multiline_string(self):
        """Test extracting text from a multiline string."""
        content = "Line 1\nLine 2\nLine 3"
        result = extract_text(content)
        assert result == "Line 1\nLine 2\nLine 3"

    def test_extract_text_from_list_with_text_parts(self):
        """Test extracting text from list with text dict parts."""
        content = [
            {"type": "text", "text": "Part 1"},
            {"type": "text", "text": "Part 2"},
            {"type": "text", "text": "Part 3"}
        ]
        result = extract_text(content)
        assert result == "Part 1Part 2Part 3"

    def test_extract_text_from_list_with_string_parts(self):
        """Test extracting text from list with string parts."""
        content = ["String 1", "String 2", "String 3"]
        result = extract_text(content)
        assert result == "String 1String 2String 3"

    def test_extract_text_from_list_with_mixed_types(self):
        """Test extracting text from list with mixed string and dict parts."""
        content = [
            "Plain string",
            {"type": "text", "text": " and dict"},
            " and another string"
        ]
        result = extract_text(content)
        assert result == "Plain string and dict and another string"

    def test_extract_text_from_empty_list(self):
        """Test extracting text from an empty list."""
        content = []
        result = extract_text(content)
        assert result == ""

    def test_extract_text_from_list_with_empty_text_dict(self):
        """Test extracting text from list with text dict containing empty text."""
        content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "Non-empty"}
        ]
        result = extract_text(content)
        assert result == "Non-empty"

    def test_extract_text_from_list_with_missing_text_key(self):
        """Test extracting text from list with dict missing 'text' key."""
        content = [
            {"type": "text"},
            {"type": "text", "text": "Has text"}
        ]
        result = extract_text(content)
        assert result == "Has text"

    def test_extract_text_from_list_with_non_text_type(self):
        """Test extracting text from list with non-text type dicts."""
        content = [
            {"type": "image", "url": "image.png"},
            {"type": "text", "text": "Text part"},
            {"type": "tool_use", "name": "tool"}
        ]
        result = extract_text(content)
        assert result == "Text part"

    def test_extract_text_from_list_with_nested_structures(self):
        """Test extracting text from list with nested dict structures."""
        content = [
            {"type": "text", "text": "Start"},
            {"type": "other", "nested": {"type": "text", "text": "Ignored"}},
            {"type": "text", "text": "End"}
        ]
        result = extract_text(content)
        assert result == "StartEnd"

    def test_extract_text_from_integer(self):
        """Test extracting text from an integer (fallback to str())."""
        content = 42
        result = extract_text(content)
        assert result == "42"

    def test_extract_text_from_float(self):
        """Test extracting text from a float (fallback to str())."""
        content = 3.14
        result = extract_text(content)
        assert result == "3.14"

    def test_extract_text_from_none(self):
        """Test extracting text from None (fallback to str())."""
        content = None
        result = extract_text(content)
        assert result == "None"

    def test_extract_text_from_boolean(self):
        """Test extracting text from a boolean (fallback to str())."""
        content = True
        result = extract_text(content)
        assert result == "True"

    def test_extract_text_from_dict_without_type(self):
        """Test extracting text from a dict without 'type' field (fallback to str())."""
        content = {"key": "value"}
        result = extract_text(content)
        assert result == "{'key': 'value'}"

    def test_extract_text_preserves_whitespace_in_strings(self):
        """Test that whitespace is preserved in string content."""
        content = "  leading and trailing  "
        result = extract_text(content)
        assert result == "  leading and trailing  "

    def test_extract_text_preserves_whitespace_in_list(self):
        """Test that whitespace is preserved in list content."""
        content = [
            {"type": "text", "text": "  spaces  "},
            {"type": "text", "text": "\ttabs\t"}
        ]
        result = extract_text(content)
        assert result == "  spaces  \ttabs\t"

    def test_extract_text_from_list_with_unicode(self):
        """Test extracting text with Unicode characters."""
        content = [
            {"type": "text", "text": "Hello 世界"},
            {"type": "text", "text": " 🌍"}
        ]
        result = extract_text(content)
        assert result == "Hello 世界 🌍"

    def test_extract_text_from_list_with_special_characters(self):
        """Test extracting text with special characters."""
        content = [
            {"type": "text", "text": "Special: !@#$%^&*()"},
            {"type": "text", "text": " <>?/\\"}
        ]
        result = extract_text(content)
        assert result == "Special: !@#$%^&*() <>?/\\"

    def test_extract_text_from_complex_mixed_list(self):
        """Test complex scenario with multiple content types."""
        content = [
            "Plain start",
            {"type": "text", "text": " text dict"},
            {"type": "image", "url": "test.jpg"},
            " plain middle",
            {"type": "text"},  # Missing text key
            {"type": "text", "text": ""},  # Empty text
            {"type": "text", "text": " end"}
        ]
        result = extract_text(content)
        assert result == "Plain start text dict plain middle end"

    def test_extract_text_handles_newlines_in_list(self):
        """Test that newlines are preserved in list content."""
        content = [
            {"type": "text", "text": "Line 1\n"},
            {"type": "text", "text": "Line 2\n"},
            {"type": "text", "text": "Line 3"}
        ]
        result = extract_text(content)
        assert result == "Line 1\nLine 2\nLine 3"
