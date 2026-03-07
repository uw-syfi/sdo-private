"""Tests for progress_summarizer module."""

from unittest.mock import Mock

from app_operator.cli_agent._progress_summarizer import ProgressSummarizer


class TestProgressSummarizer:
    """Test ProgressSummarizer class."""

    def test_initialization(self):
        """Test basic initialization."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)
        assert summarizer.agent_generate_fn == agent_fn
        assert summarizer.initial_delay == 15.0
        assert summarizer.summary_interval == 30.0
        assert summarizer.start_time is None
        assert summarizer.last_summary_time is None

    def test_initialization_with_custom_values(self):
        """Test initialization with custom parameters."""
        agent_fn = Mock()
        time_fn = Mock(return_value=100.0)
        summarizer = ProgressSummarizer(agent_fn, initial_delay=10.0, summary_interval=20.0, time_func=time_fn)
        assert summarizer.initial_delay == 10.0
        assert summarizer.summary_interval == 20.0
        assert summarizer.time_func == time_fn

    def test_start_without_explicit_time(self):
        """Test start() uses time_func when start_time not provided."""
        agent_fn = Mock()
        time_fn = Mock(return_value=100.0)
        summarizer = ProgressSummarizer(agent_fn, time_func=time_fn)

        summarizer.start()

        assert summarizer.start_time == 100.0
        assert summarizer.last_summary_time == 100.0
        time_fn.assert_called_once()

    def test_start_with_explicit_time(self):
        """Test start() with explicit start_time."""
        agent_fn = Mock()
        time_fn = Mock(return_value=200.0)
        summarizer = ProgressSummarizer(agent_fn, time_func=time_fn)

        summarizer.start(start_time=150.0)

        assert summarizer.start_time == 150.0
        assert summarizer.last_summary_time == 150.0
        # time_fn should not be called when start_time is explicit
        time_fn.assert_not_called()

    def test_should_summarize_before_start(self):
        """Test should_summarize() returns False before start() is called."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        assert summarizer.should_summarize() is False

    def test_should_summarize_before_initial_delay(self):
        """Test should_summarize() returns False before initial delay elapsed."""
        agent_fn = Mock()
        current_time = [100.0]  # Use list to allow modification in nested function

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, initial_delay=15.0, time_func=time_func)
        summarizer.start(start_time=100.0)

        # Time at 110.0 (only 10s elapsed, less than 15s initial delay)
        current_time[0] = 110.0
        assert summarizer.should_summarize() is False

    def test_should_summarize_after_initial_delay_first_summary(self):
        """Test should_summarize() returns True after initial delay AND interval."""
        agent_fn = Mock()
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, initial_delay=15.0, summary_interval=30.0, time_func=time_func)
        summarizer.start(start_time=100.0)

        # Time at 130.0 (30s elapsed, satisfies both initial delay and interval)
        current_time[0] = 130.0
        assert summarizer.should_summarize() is True

    def test_should_summarize_respects_summary_interval(self):
        """Test should_summarize() respects summary_interval for subsequent summaries."""
        agent_fn = Mock()
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, initial_delay=10.0, summary_interval=30.0, time_func=time_func)
        summarizer.start(start_time=100.0)

        # After both initial delay and interval (30s elapsed, >10s initial, >=30s interval)
        current_time[0] = 130.0
        assert summarizer.should_summarize() is True

        # Simulate a summary being generated (updates last_summary_time)
        summarizer.last_summary_time = 130.0

        # Only 10s later (less than 30s interval)
        current_time[0] = 140.0
        assert summarizer.should_summarize() is False

        # Exactly 30s later
        current_time[0] = 160.0
        assert summarizer.should_summarize() is True

    def test_should_summarize_boundary_exactly_at_interval(self):
        """Test should_summarize() boundary condition at exact interval."""
        agent_fn = Mock()
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, initial_delay=10.0, summary_interval=30.0, time_func=time_func)
        summarizer.start(start_time=100.0)
        summarizer.last_summary_time = 100.0

        # Exactly at 30s (satisfies >= interval and > initial delay)
        current_time[0] = 130.0
        assert summarizer.should_summarize() is True

        # Just before 30s interval
        current_time[0] = 129.9
        summarizer.last_summary_time = 100.0  # Reset
        assert summarizer.should_summarize() is False

    def test_summarize_with_empty_output(self):
        """Test summarize() does nothing with empty output."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)
        summarizer.start(start_time=100.0)

        summarizer.summarize("")

        agent_fn.assert_not_called()

    def test_summarize_with_whitespace_only_output(self):
        """Test summarize() does nothing with whitespace-only output."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)
        summarizer.start(start_time=100.0)

        summarizer.summarize("   \n\t  ")

        agent_fn.assert_not_called()

    def test_summarize_calls_agent_with_correct_parameters(self):
        """Test summarize() calls agent with correct prompt and parameters."""
        agent_fn = Mock(return_value="<output_msg>Summary text</output_msg>")
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, time_func=time_func)
        summarizer.start(start_time=100.0)
        current_time[0] = 110.0

        summarizer.summarize("Some deployment output")

        # Agent should be called with silent=True and timeout=30
        agent_fn.assert_called_once()
        call_args = agent_fn.call_args
        prompt, silent, timeout = call_args[0]

        assert "Some deployment output" in prompt
        assert silent is True
        assert timeout == 30

    def test_summarize_updates_last_summary_time(self):
        """Test summarize() updates last_summary_time after successful summary."""
        agent_fn = Mock(return_value="<output_msg>Summary</output_msg>")
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, time_func=time_func)
        summarizer.start(start_time=100.0)

        current_time[0] = 120.0
        summarizer.summarize("Output")

        assert summarizer.last_summary_time == 120.0

    def test_summarize_handles_agent_exception_gracefully(self):
        """Test summarize() handles agent exceptions without crashing."""
        agent_fn = Mock(side_effect=RuntimeError("Agent error"))
        summarizer = ProgressSummarizer(agent_fn)
        summarizer.start(start_time=100.0)

        # Should not raise exception
        summarizer.summarize("Some output")

    def test_summarize_handles_agent_timeout(self):
        """Test summarize() handles timeout scenario gracefully."""
        agent_fn = Mock(side_effect=TimeoutError("Agent timeout"))
        summarizer = ProgressSummarizer(agent_fn)
        summarizer.start(start_time=100.0)

        # Should not raise exception
        summarizer.summarize("Some output")

    def test_extract_summary_with_valid_xml(self):
        """Test _extract_summary() with valid XML tags."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "Some preamble\n<output_msg>This is the summary</output_msg>\nSome postamble"
        result = summarizer._extract_summary(response)

        assert result == "This is the summary"

    def test_extract_summary_with_multiline_content(self):
        """Test _extract_summary() with multiline content in XML."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg>Line 1\nLine 2\nLine 3</output_msg>"
        result = summarizer._extract_summary(response)

        assert result == "Line 1\nLine 2\nLine 3"

    def test_extract_summary_with_whitespace(self):
        """Test _extract_summary() strips whitespace."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg>  \n  Summary with spaces  \n  </output_msg>"
        result = summarizer._extract_summary(response)

        assert result == "Summary with spaces"

    def test_extract_summary_missing_tags(self):
        """Test _extract_summary() returns None when tags are missing."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "No XML tags here"
        result = summarizer._extract_summary(response)

        assert result is None

    def test_extract_summary_malformed_opening_tag(self):
        """Test _extract_summary() handles malformed opening tag."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msgSummary</output_msg>"
        result = summarizer._extract_summary(response)

        assert result is None

    def test_extract_summary_malformed_closing_tag(self):
        """Test _extract_summary() handles malformed closing tag."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg>Summary</output_msg"
        result = summarizer._extract_summary(response)

        assert result is None

    def test_extract_summary_empty_content(self):
        """Test _extract_summary() with empty content between tags."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg></output_msg>"
        result = summarizer._extract_summary(response)

        # Empty string gets stripped to empty string
        assert result == ""

    def test_extract_summary_nested_tags(self):
        """Test _extract_summary() with nested content."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg>Summary with <inner>nested</inner> tags</output_msg>"
        result = summarizer._extract_summary(response)

        assert result == "Summary with <inner>nested</inner> tags"

    def test_extract_summary_multiple_occurrences(self):
        """Test _extract_summary() with multiple XML tags (should match first)."""
        agent_fn = Mock()
        summarizer = ProgressSummarizer(agent_fn)

        response = "<output_msg>First</output_msg> other text <output_msg>Second</output_msg>"
        result = summarizer._extract_summary(response)

        assert result == "First"

    def test_integration_summarize_with_no_xml_in_response(self):
        """Test summarize() when agent response has no XML tags."""
        agent_fn = Mock(return_value="Plain text response without XML")
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, time_func=time_func)
        summarizer.start(start_time=100.0)
        current_time[0] = 110.0

        # Should not crash even if no summary is extracted
        summarizer.summarize("Some output")

        # last_summary_time should still be updated
        assert summarizer.last_summary_time == 110.0

    def test_integration_full_cycle(self):
        """Test full cycle: start, check, summarize, check again."""
        agent_fn = Mock(return_value="<output_msg>Progress update</output_msg>")
        current_time = [100.0]

        def time_func():
            return current_time[0]

        summarizer = ProgressSummarizer(agent_fn, initial_delay=10.0, summary_interval=30.0, time_func=time_func)

        # Start
        summarizer.start(start_time=100.0)
        assert summarizer.should_summarize() is False

        # After both initial delay and interval (30s)
        current_time[0] = 130.0
        assert summarizer.should_summarize() is True

        # Generate summary
        summarizer.summarize("Deployment output")
        agent_fn.assert_called_once()

        # Check immediately after (should be False)
        current_time[0] = 131.0
        assert summarizer.should_summarize() is False

        # Check after interval (30s from last summary at 130)
        current_time[0] = 160.0
        assert summarizer.should_summarize() is True
