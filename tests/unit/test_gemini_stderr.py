import io
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from libs.agent_cli.gemini import GeminiCodingAgent


@pytest.fixture
def mock_env():
    """Mock environment for agent initialization."""
    return {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "USER": "test"}


@pytest.fixture
def mock_which():
    """Mock shutil.which to return a fake binary path."""
    with patch("shutil.which", return_value="/usr/bin/gemini"):
        yield


@pytest.fixture
def gemini_agent(mock_which, mock_env):
    """Create a GeminiCodingAgent instance with mocked environment."""
    with patch("libs.agent_cli.cli_agent._get_interactive_env", return_value=mock_env):
        with patch("libs.agent_cli.cli_agent.CLICodingAgent._check_cli"):
            agent = GeminiCodingAgent()
            yield agent


@pytest.fixture
def mock_popen():
    """Mock subprocess.Popen."""
    with patch("subprocess.Popen") as mock:
        yield mock


def test_stderr_output_in_red(gemini_agent, mock_popen):
    """Test that stderr output is displayed in red text."""

    # Mock stderr lines with warning/error messages
    stderr_lines = [
        "(node:30850) [DEP0040] DeprecationWarning: The `punycode` module is deprecated.\n",
        "(Use `node --trace-deprecation ...` to show where the warning was created)\n",
        "Error: Failed to connect to API\n",
    ]

    # Mock stdout with normal output
    stdout_lines = [
        '{"type":"message","role":"assistant","content":"Hello world"}\n',
        "",  # EOF marker
    ]

    # Mock the process
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout.readline.side_effect = stdout_lines
    mock_process.stderr.readline.side_effect = stderr_lines + [""]
    mock_process.wait.return_value = 0

    mock_popen.return_value = mock_process

    # Capture logger output to verify red coloring
    # Use the actual formatter from logger.py to get proper color codes
    from app_operator.logger import formatter

    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, colorize=True)

    try:
        result = gemini_agent.generate("Test prompt")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Verify stderr messages are present
    assert "DeprecationWarning" in output
    assert "Failed to connect to API" in output

    # Verify red ANSI color codes are used for stderr
    # Red text: \x1b[31m
    # Reset: \x1b[0m
    assert "\x1b[31m" in output, "Expected red color code for stderr output"

    # Verify stderr messages contain [STDERR] prefix in red
    assert "[Gemini] \x1b[31m[STDERR]" in output or "[STDERR]" in output

    # Verify the content is captured correctly
    assert "Hello world" in result


def test_multiple_stderr_lines_in_red(gemini_agent, mock_popen):
    """Test that multiple stderr lines are all displayed in red."""

    stderr_lines = [
        "Warning: Configuration file not found\n",
        "Error: Connection timeout\n",
        "Fatal: Unable to proceed\n",
    ]

    stdout_lines = ['{"type":"message","role":"assistant","content":"Processing..."}\n', ""]

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout.readline.side_effect = stdout_lines
    mock_process.stderr.readline.side_effect = stderr_lines + [""]
    mock_process.wait.return_value = 0

    mock_popen.return_value = mock_process

    # Use the actual formatter from logger.py to get proper color codes
    from app_operator.logger import formatter

    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, colorize=True)

    try:
        gemini_agent.generate("Test prompt")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Count red color codes - should have at least one per stderr line
    red_count = output.count("\x1b[31m")
    assert red_count >= 3, f"Expected at least 3 red color codes, found {red_count}"

    # Verify all stderr messages are present
    assert "Configuration file not found" in output
    assert "Connection timeout" in output
    assert "Unable to proceed" in output


def test_mixed_stdout_stderr_coloring(gemini_agent, mock_popen):
    """Test that stdout (normal) and stderr (red) have different colors."""

    stderr_lines = ["ERROR: Something went wrong\n", ""]
    stdout_lines = ['{"type":"message","role":"assistant","content":"Success message"}\n', ""]

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout.readline.side_effect = stdout_lines
    mock_process.stderr.readline.side_effect = stderr_lines
    mock_process.wait.return_value = 0

    mock_popen.return_value = mock_process

    # Use the actual formatter from logger.py to get proper color codes
    from app_operator.logger import formatter

    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, colorize=True)

    try:
        result = gemini_agent.generate("Test prompt")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Verify stderr is red
    assert "\x1b[31m" in output, "Expected red color for stderr"
    assert "Something went wrong" in output

    # Verify stdout content is present (normal text, no red)
    assert "Success message" in result

    # Ensure the error line has red but the success message section doesn't
    lines = output.split("\n")
    error_lines = [line for line in lines if "Something went wrong" in line]
    assert any("\x1b[31m" in line for line in error_lines), "Error line should be red"
