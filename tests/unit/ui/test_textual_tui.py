"""Tests for textual_tui module."""
from unittest.mock import Mock

from app_operator.ui.textual_tui import TextualOperatorUI


class TestTextualOperatorUI:
    """Test TextualOperatorUI class."""

    def test_initialization(self):
        """Test basic initialization."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)
        assert ui.app == mock_app

    def test_set_stage_calls_app_method(self):
        """Test set_stage() calls app method via call_from_thread."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.set_stage("deployment", detail="Starting deployment")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.update_stage,
            "deployment",
            "Starting deployment"
        )

    def test_set_stage_without_detail(self):
        """Test set_stage() without detail parameter."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.set_stage("monitoring")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.update_stage,
            "monitoring",
            None
        )

    def test_set_stage_without_status(self):
        """Test set_stage() without status parameter (status is optional)."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.set_stage("analysis", detail="Analyzing code", status=None)

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.update_stage,
            "analysis",
            "Analyzing code"
        )

    def test_log_with_default_level(self):
        """Test log() with default info level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.log("Test message")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_log_message,
            "Test message",
            "info"
        )

    def test_log_with_error_level(self):
        """Test log() with error level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.log("Error message", level="error")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_log_message,
            "Error message",
            "error"
        )

    def test_log_with_warning_level(self):
        """Test log() with warning level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.log("Warning message", level="warning")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_log_message,
            "Warning message",
            "warning"
        )

    def test_log_with_success_level(self):
        """Test log() with success level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.log("Success message", level="success")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_log_message,
            "Success message",
            "success"
        )

    def test_on_thinking(self):
        """Test on_thinking() calls app method."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_thinking("Analyzing the repository...")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_thinking,
            "Analyzing the repository..."
        )

    def test_on_thinking_with_empty_text(self):
        """Test on_thinking() with empty text."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_thinking("")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_thinking,
            ""
        )

    def test_on_thinking_with_multiline_text(self):
        """Test on_thinking() with multiline text."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        thinking_text = "Thinking line 1\nThinking line 2\nThinking line 3"
        ui.on_thinking(thinking_text)

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_thinking,
            thinking_text
        )

    def test_on_tool_call_with_dict_args(self):
        """Test on_tool_call() with dict arguments."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        tool_args = {"command": "ls -la", "timeout": 30}
        ui.on_tool_call("bash", args=tool_args)

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_call,
            "bash",
            tool_args
        )

    def test_on_tool_call_with_string_args(self):
        """Test on_tool_call() with string arguments."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_call("read_file", args="/path/to/file.txt")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_call,
            "read_file",
            "/path/to/file.txt"
        )

    def test_on_tool_call_without_args(self):
        """Test on_tool_call() without arguments."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_call("list_files")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_call,
            "list_files",
            None
        )

    def test_on_tool_call_with_none_args(self):
        """Test on_tool_call() with explicit None args."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_call("health_check", args=None)

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_call,
            "health_check",
            None
        )

    def test_on_tool_result_with_all_parameters(self):
        """Test on_tool_result() with all parameters."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_result(
            tool="bash",
            stdout="Command output",
            stderr="",
            exit_code=0,
            duration=1.5
        )

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_result,
            "bash",
            "Command output",
            "",
            0,
            1.5
        )

    def test_on_tool_result_with_defaults(self):
        """Test on_tool_result() with default parameters."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_result(tool="read_file")

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_result,
            "read_file",
            "",
            "",
            None,
            None
        )

    def test_on_tool_result_with_error(self):
        """Test on_tool_result() with error output."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.on_tool_result(
            tool="bash",
            stdout="",
            stderr="Command failed",
            exit_code=1,
            duration=0.5
        )

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_result,
            "bash",
            "",
            "Command failed",
            1,
            0.5
        )

    def test_on_tool_result_with_multiline_output(self):
        """Test on_tool_result() with multiline output."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        stdout = "Line 1\nLine 2\nLine 3"
        stderr = "Error line 1\nError line 2"
        ui.on_tool_result(
            tool="test_tool",
            stdout=stdout,
            stderr=stderr,
            exit_code=1
        )

        mock_app.call_from_thread.assert_called_once_with(
            mock_app.add_tool_result,
            "test_tool",
            stdout,
            stderr,
            1,
            None
        )

    def test_close_with_success(self):
        """Test close() with successful status."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close(status="completed", exit_code=0)

        # Verify the message contains both status and exit code
        mock_app.call_from_thread.assert_called_once()
        call_args = mock_app.call_from_thread.call_args
        assert call_args[0][0] == mock_app.add_log_message
        message = call_args[0][1]
        level = call_args[0][2]

        assert "completed" in message
        assert "0" in message
        assert level == "success"

    def test_close_with_error(self):
        """Test close() with error status."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close(status="failed", exit_code=1)

        mock_app.call_from_thread.assert_called_once()
        call_args = mock_app.call_from_thread.call_args
        message = call_args[0][1]
        level = call_args[0][2]

        assert "failed" in message
        assert "1" in message
        assert level == "error"

    def test_close_without_parameters(self):
        """Test close() without parameters."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close()

        mock_app.call_from_thread.assert_called_once()
        call_args = mock_app.call_from_thread.call_args
        message = call_args[0][1]

        assert "None" in message

    def test_close_with_none_exit_code(self):
        """Test close() with None exit code."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close(status="interrupted", exit_code=None)

        mock_app.call_from_thread.assert_called_once()
        call_args = mock_app.call_from_thread.call_args
        message = call_args[0][1]
        # None exit code should result in error level
        level = call_args[0][2]

        assert "interrupted" in message
        assert level == "error"

    def test_close_exit_code_zero_results_in_success_level(self):
        """Test that close() with exit_code=0 results in success level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close(status="done", exit_code=0)

        call_args = mock_app.call_from_thread.call_args
        level = call_args[0][2]
        assert level == "success"

    def test_close_non_zero_exit_code_results_in_error_level(self):
        """Test that close() with non-zero exit_code results in error level."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.close(status="done", exit_code=1)

        call_args = mock_app.call_from_thread.call_args
        level = call_args[0][2]
        assert level == "error"

    def test_multiple_calls_to_app_methods(self):
        """Test multiple sequential calls to UI methods."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        ui.set_stage("init")
        ui.log("Starting")
        ui.on_thinking("Analyzing...")
        ui.on_tool_call("bash", {"command": "ls"})
        ui.on_tool_result("bash", stdout="output", exit_code=0)

        assert mock_app.call_from_thread.call_count == 5

    def test_thread_safety_all_methods_use_call_from_thread(self):
        """Test that all methods use call_from_thread for thread safety."""
        mock_app = Mock()
        ui = TextualOperatorUI(mock_app)

        # Test all public methods
        ui.set_stage("test")
        ui.log("test")
        ui.on_thinking("test")
        ui.on_tool_call("test")
        ui.on_tool_result("test")
        ui.close()

        # All 6 calls should use call_from_thread
        assert mock_app.call_from_thread.call_count == 6
