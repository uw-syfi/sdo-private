"""
Test that all CLI agents properly pass the prompt to their underlying CLI tools.

This test suite ensures that each agent type (Claude, Codex, Gemini, Opencode)
correctly passes the user's prompt either as a command-line argument or via stdin,
depending on the CLI tool's requirements.

Design:
- Claude and Opencode: Pass prompt as command-line argument
- Codex and Gemini: Pass prompt via stdin
"""

from unittest.mock import MagicMock, patch

import pytest

from libs.agent_cli.claude import ClaudeCodeCodingAgent
from libs.agent_cli.codex import CodexCodingAgent
from libs.agent_cli.gemini import GeminiCodingAgent
from libs.agent_cli.opencode import OpencodeCodingAgent


class MockProcess:
    """Mock subprocess.Popen for testing prompt passing."""

    def __init__(self, *args, **kwargs):
        self.pid = 12345
        self.returncode = 0
        self.stdout = MagicMock()
        self.stderr = MagicMock()
        self.stdin = MagicMock()
        self.cmd = args[0] if args else []

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


@pytest.fixture
def mock_which():
    """Mock shutil.which to return a fake binary path."""

    def which_impl(name, path=None):
        return f"/usr/bin/{name}"

    return which_impl


@pytest.fixture(
    params=[
        ("claude", ClaudeCodeCodingAgent, True),  # (name, class, has_prompt_in_cmd)
        ("codex", CodexCodingAgent, False),
        ("gemini", GeminiCodingAgent, False),
        ("opencode", OpencodeCodingAgent, True),
    ],
    ids=["claude", "codex", "gemini", "opencode"],
)
def agent_info(request):
    """Parameterized fixture for all agent types with prompt passing info."""
    return request.param


# ============================================================================
# PROMPT PASSING IN COMMAND LINE TESTS
# ============================================================================


def test_agent_includes_prompt_in_command_when_required(agent_info, mock_which):
    """Test agents that require prompt in command line include it."""
    agent_name, agent_class, has_prompt_in_cmd = agent_info
    test_prompt = "Write a hello world function"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = agent_class()
                agent.generate(test_prompt, silent=True)

    assert len(captured_commands) > 0
    command = captured_commands[0]

    if has_prompt_in_cmd:
        # For Claude and Opencode, prompt should be in the command
        # Convert command list to string for easier checking
        cmd_str = " ".join(command)
        assert test_prompt in cmd_str, (
            f"{agent_name} should include prompt '{test_prompt}' in command, "
            f"but got: {cmd_str}"
        )
    # Note: For agents that don't include prompt in command (Codex, Gemini),
    # they pass it via stdin which is tested separately


def test_agent_passes_prompt_via_stdin(agent_info, mock_which):
    """Test all agents pass prompt via stdin."""
    agent_name, agent_class, _ = agent_info
    test_prompt = "Create a function to sort a list"

    stdin_writes = []

    def track_popen(*args, **kwargs):
        mock_process = MockProcess(*args, **kwargs)

        def track_write(data):
            stdin_writes.append(data)

        mock_process.stdin.write = track_write
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = agent_class()
                agent.generate(test_prompt, silent=True)

    # All agents should write to stdin
    assert len(stdin_writes) > 0, f"{agent_name} should write prompt to stdin"
    assert test_prompt in stdin_writes[0], (
        f"{agent_name} should pass prompt '{test_prompt}' to stdin, "
        f"but got: {stdin_writes}"
    )


# ============================================================================
# COMMAND CONSTRUCTION TESTS
# ============================================================================


def test_claude_command_structure(mock_which):
    """Test Claude agent constructs correct command with prompt."""
    test_prompt = "Write a test function"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = ClaudeCodeCodingAgent()
                agent.generate(test_prompt, silent=True)

    assert len(captured_commands) > 0
    cmd = captured_commands[0]

    # Check expected flags
    assert "-p" in cmd, "Claude should use -p flag for print mode"
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--verbose" in cmd

    # Check prompt is in command
    cmd_str = " ".join(cmd)
    assert test_prompt in cmd_str


def test_codex_command_structure(mock_which):
    """Test Codex agent constructs correct command."""
    test_prompt = "Write a test function"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = CodexCodingAgent()
                agent.generate(test_prompt, silent=True)

    assert len(captured_commands) > 0
    cmd = captured_commands[0]

    # Check expected command structure
    assert "codex" in cmd[0]  # Binary name
    assert "exec" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_gemini_command_structure(mock_which):
    """Test Gemini agent constructs correct command."""
    test_prompt = "Write a test function"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = GeminiCodingAgent()
                agent.generate(test_prompt, silent=True)

    assert len(captured_commands) > 0
    cmd = captured_commands[0]

    # Check expected flags
    assert "-y" in cmd  # YOLO mode
    assert "-o" in cmd
    assert "stream-json" in cmd


def test_opencode_command_structure(mock_which):
    """Test Opencode agent constructs correct command with prompt."""
    test_prompt = "Write a test function"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = OpencodeCodingAgent()
                agent.generate(test_prompt, silent=True)

    assert len(captured_commands) > 0
    cmd = captured_commands[0]

    # Check expected command structure
    assert "opencode" in cmd[0]  # Binary name
    assert "run" in cmd
    assert "--format=json" in cmd

    # Check prompt is in command
    cmd_str = " ".join(cmd)
    assert test_prompt in cmd_str


# ============================================================================
# MODEL PARAMETER TESTS
# ============================================================================


def test_agent_includes_model_in_command_when_specified(agent_info, mock_which):
    """Test agents include --model flag when model is specified."""
    agent_name, agent_class, _ = agent_info
    test_model = "custom-model-v1"

    captured_commands = []

    def track_popen(*args, **kwargs):
        captured_commands.append(args[0] if args else [])
        mock_process = MockProcess(*args, **kwargs)
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                # OpencodeCodingAgent has a default model, so skip for this test
                if agent_class == OpencodeCodingAgent:
                    pytest.skip("Opencode has default model behavior")

                agent = agent_class(model=test_model)
                agent.generate("test prompt", silent=True)

    assert len(captured_commands) > 0
    cmd = captured_commands[0]

    # All agents should include --model flag when specified
    assert "--model" in cmd, f"{agent_name} should include --model flag"
    # Find the index of --model and check the next element is our model
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == test_model, (
        f"{agent_name} should use model '{test_model}', but got: {cmd[model_idx + 1]}"
    )


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


def test_agent_handles_multiline_prompts(agent_info, mock_which):
    """Test agents correctly handle prompts with multiple lines."""
    agent_name, agent_class, _ = agent_info
    test_prompt = """Write a function that:
1. Takes a list of numbers
2. Filters even numbers
3. Returns the sum"""

    stdin_writes = []

    def track_popen(*args, **kwargs):
        mock_process = MockProcess(*args, **kwargs)

        def track_write(data):
            stdin_writes.append(data)

        mock_process.stdin.write = track_write
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = agent_class()
                agent.generate(test_prompt, silent=True)

    # Verify the full multiline prompt is passed via stdin
    assert len(stdin_writes) > 0
    assert test_prompt in stdin_writes[0], (
        f"{agent_name} should pass full multiline prompt via stdin"
    )


def test_agent_handles_special_characters_in_prompt(agent_info, mock_which):
    """Test agents handle prompts with special characters."""
    agent_name, agent_class, _ = agent_info
    test_prompt = "Fix bug in \"auth.js\" where user's password isn't validated"

    stdin_writes = []

    def track_popen(*args, **kwargs):
        mock_process = MockProcess(*args, **kwargs)

        def track_write(data):
            stdin_writes.append(data)

        mock_process.stdin.write = track_write
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = agent_class()
                agent.generate(test_prompt, silent=True)

    # Verify prompt with special characters is passed correctly
    assert len(stdin_writes) > 0
    assert test_prompt in stdin_writes[0], (
        f"{agent_name} should handle special characters in prompt"
    )


def test_agent_handles_empty_prompt(agent_info, mock_which):
    """Test agents handle empty prompts gracefully."""
    agent_name, agent_class, _ = agent_info
    test_prompt = ""

    stdin_writes = []

    def track_popen(*args, **kwargs):
        mock_process = MockProcess(*args, **kwargs)

        def track_write(data):
            stdin_writes.append(data)

        mock_process.stdin.write = track_write
        mock_process.stdout.readline.side_effect = ["output\n", ""]
        mock_process.stderr.readline.side_effect = [""]
        return mock_process

    with patch("shutil.which", side_effect=mock_which):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("subprocess.Popen", side_effect=track_popen):
                agent = agent_class()
                # Should not raise an exception
                agent.generate(test_prompt, silent=True)

    # Verify stdin write was attempted even with empty prompt
    assert len(stdin_writes) > 0
