import io
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from loguru import logger

from app_operator.cli_agent.backend.gemini import GeminiCodingAgent

# Define the fixture path relative to this test file or project root
FIXTURE_PATH = Path("tests/fixtures/gemini/example_stream_json.txt")


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
    with patch(
        "app_operator.cli_agent.backend.cli_agent._get_interactive_env",
        return_value=mock_env,
    ):
        with patch(
            "app_operator.cli_agent.backend.cli_agent.CLICodingAgent._check_cli"
        ):
            agent = GeminiCodingAgent()
            yield agent


@pytest.fixture
def mock_popen():
    """Mock subprocess.Popen."""
    with patch("subprocess.Popen") as mock:
        yield mock


def test_generate_from_fixture(gemini_agent, mock_popen):
    """Test parsing a real stream dump from a fixture file."""

    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture file not found at {FIXTURE_PATH}")

    # Read the fixture file
    with open(FIXTURE_PATH, "r") as f:
        fixture_lines = f.readlines()

    # Mock the process output
    mock_process = MagicMock()
    mock_process.returncode = 0
    # readline side effect needs to return each line, then empty string to signal EOF
    mock_process.stdout.readline.side_effect = fixture_lines + [""]
    mock_process.stderr.readline.return_value = ""
    mock_process.wait.return_value = 0

    mock_popen.return_value = mock_process

    # Capture stdout to verify rendering
    captured_stdout = io.StringIO()
    handler_id = logger.add(captured_stdout, format="{message}")
    try:
        result = gemini_agent.generate("Test prompt")
    finally:
        logger.remove(handler_id)

    output = captured_stdout.getvalue()

    # Verify key interactions from the log

    # 1. Prefixing on text content
    assert "[Gemini] I will start by analyzing" in output

    # 2. Tool Use (Blue)
    # [Gemini] [Tool Use] read_file {'file_path': 'docker-compose.yml'}
    assert "[Gemini] \x1b[34m[Tool Use] read_file" in output
    assert "docker-compose.yml" in output

    # 3. Tool Result (Green)
    # The first read_file output is empty string
    # Should print: [Gemini] read_file ran successfully
    assert "[Gemini] \x1b[32mread_file ran successfully\x1b[0m" in output

    # 4. Another Tool Result with content (glob found files)
    # {"output":"Found 1 matching file(s)"}
    assert "[Gemini] \x1b[32m[Tool Result] Found 1 matching file(s)" in output

    # 5. Verify accumulation of content
    # The agent accumulates the 'content' fields from 'message' events
    assert "I will start by analyzing" in result
    assert "I will check the configuration" in result

    # 6. Verify non-JSON lines (YOLO mode) are printed
    assert "[Gemini] YOLO mode is enabled." in output
