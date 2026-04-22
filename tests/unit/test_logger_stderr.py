import io
from unittest.mock import patch

import pytest
from agentshim.gemini import GeminiCodingAgent
from loguru import logger

from app_operator.core import formatter


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
    with patch("agentshim.cli_agent.get_interactive_env", return_value=mock_env):
        with patch("agentshim.cli_agent.CLICodingAgent._check_cli"):
            agent = GeminiCodingAgent()
            yield agent


@pytest.fixture
def mock_popen():
    """Mock subprocess.Popen."""
    with patch("subprocess.Popen") as mock:
        yield mock


def test_stderr_output_with_agent_prefix_is_red(gemini_agent):
    """Test that stderr output with agent_prefix is colored red."""
    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, level="INFO", colorize=True)
    try:
        # Create a session and simulate stderr processing
        session = gemini_agent._create_session(["test", "command"], silent=False)
        session._process_stderr("Error: something went wrong\n")
        session._process_stderr("Warning: deprecated feature\n")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Verify red color codes are present
    # Red ANSI code: \x1b[31m, Reset: \x1b[0m
    assert "\x1b[31m" in output
    assert "\x1b[0m" in output

    # Verify the stderr messages are present
    assert "[STDERR] Error: something went wrong" in output
    assert "[STDERR] Warning: deprecated feature" in output

    # Verify agent prefix is present
    assert "[Gemini]" in output


def test_stderr_output_without_agent_prefix_is_red():
    """Test that stderr output without agent_prefix is colored red."""
    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, level="INFO", colorize=True)
    try:
        # Log stderr without agent_prefix
        logger.bind(stderr=True).info("Error: direct stderr message")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Verify red color codes are present
    assert "\x1b[31m" in output
    assert "\x1b[0m" in output

    # Verify the message is present
    assert "Error: direct stderr message" in output


def test_stderr_output_multiple_lines_with_agent_prefix(gemini_agent):
    """Test that multiple stderr lines with agent_prefix are all colored red."""
    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, level="INFO", colorize=True)
    try:
        session = gemini_agent._create_session(["test", "command"], silent=False)
        # Process multiple stderr lines
        for line in ["Line 1\n", "Line 2\n", "Line 3\n"]:
            session._process_stderr(line)
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Count red color codes - should have at least 3 (one per line)
    red_count = output.count("\x1b[31m")
    assert red_count >= 3

    # Verify all messages are present
    assert "[STDERR] Line 1" in output
    assert "[STDERR] Line 2" in output
    assert "[STDERR] Line 3" in output


def test_stdout_output_not_red_with_agent_prefix(gemini_agent):
    """Test that stdout output with agent_prefix is NOT colored red."""
    captured_output = io.StringIO()
    handler_id = logger.add(captured_output, format=formatter, level="INFO", colorize=True)
    try:
        session = gemini_agent._create_session(["test", "command"], silent=False)
        # Process stdout (should not be red)
        session._process_stdout("Normal output line\n")
    finally:
        logger.remove(handler_id)

    output = captured_output.getvalue()

    # Verify stdout message is present
    assert "Normal output line" in output

    # Verify red color codes are NOT present for stdout
    assert "\x1b[31m" not in output
