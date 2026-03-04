import pytest
from unittest.mock import MagicMock
from libs.agent_cli.claude import ClaudeCodeCodingAgent, ClaudeGenerationSession
from libs.agent_cli.cli_agent import CLICodingAgent
from app_operator.trajectory import NullTrajectoryRecorder


@pytest.fixture
def mock_binaries(monkeypatch):
    """Mock binary discovery and CLI check."""
    monkeypatch.setattr(
        "libs.agent_cli.cli_agent.shutil.which",
        lambda cmd, path=None: f"/usr/local/bin/{cmd}",
    )
    monkeypatch.setattr(CLICodingAgent, "_check_cli", lambda self: None)


@pytest.fixture
def agent(mock_binaries):
    """Create a ClaudeCodeCodingAgent with mocked binaries."""
    return ClaudeCodeCodingAgent(model="test-model")


class TestClaudeCodeCodingAgentInit:
    """Tests for ClaudeCodeCodingAgent initialization."""

    def test_binary_name_is_claude(self, agent):
        assert agent.binary_name == "claude"

    def test_binary_path_resolved(self, agent):
        assert agent.binary_path == "/usr/local/bin/claude"

    def test_claude_path_property(self, agent):
        """claude_path is a backward-compatible alias for binary_path."""
        assert agent.claude_path == agent.binary_path

    def test_model_stored(self, agent):
        assert agent.model == "test-model"

    def test_default_model_is_none(self, mock_binaries):
        agent = ClaudeCodeCodingAgent()
        assert agent.model is None

    def test_log_prefix(self, agent):
        assert agent._log_prefix == "[Claude]"

    def test_binary_not_found_raises_runtime_error(self, monkeypatch):
        monkeypatch.setattr(
            "libs.agent_cli.cli_agent.shutil.which",
            lambda cmd, path=None: None,
        )
        with pytest.raises(RuntimeError, match="claude binary not found"):
            ClaudeCodeCodingAgent()


class TestClaudeCommandConstruction:
    """Tests for _get_command method."""

    def test_command_includes_required_flags(self, agent):
        cmd = agent._get_command("test prompt")
        assert agent.binary_path in cmd
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd

    def test_command_includes_model_when_set(self, agent):
        cmd = agent._get_command("test prompt")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "test-model"

    def test_command_omits_model_when_none(self, mock_binaries):
        agent = ClaudeCodeCodingAgent(model=None)
        cmd = agent._get_command("test prompt")
        assert "--model" not in cmd

    def test_command_includes_prompt(self, agent):
        cmd = agent._get_command("deploy the app")
        assert "deploy the app" in cmd


class TestClaudeGenerationSession:
    """Tests for ClaudeGenerationSession event processing."""

    def _make_session(self, event_handler=None, recorder=None):
        return ClaudeGenerationSession(
            binary_name="claude",
            env={},
            log_prefix="[Claude]",
            cmd=["claude", "-p"],
            logger=MagicMock(),
            silent=True,
            recorder=recorder or NullTrajectoryRecorder(),
            event_handler=event_handler,
        )

    def test_process_stdout_parses_text_event(self):
        session = self._make_session()
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n'
        session._process_stdout(line)
        assert "hello" in session.stdout_lines

    def test_process_stdout_parses_tool_use_event(self):
        session = self._make_session()
        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","name":"Bash","id":"t1","input":{"cmd":"ls"}}]}}\n'
        )
        session._process_stdout(line)
        assert "t1" in session.tool_map
        assert session.tool_map["t1"] == "Bash"

    def test_process_stdout_parses_tool_result_event(self):
        session = self._make_session()
        # Set up tool map first
        session.tool_map["t1"] = "Bash"
        session.tool_start_times["t1"] = 1000.0
        session.tool_args["t1"] = {"cmd": "ls"}

        line = (
            '{"type":"user","message":{"content":'
            '[{"type":"tool_result","tool_use_id":"t1","content":"file1.txt"}]}}\n'
        )
        session._process_stdout(line)
        # Tool result was processed (recorder recorded it via NullTrajectoryRecorder)

    def test_process_stdout_parses_result_event(self):
        session = self._make_session()
        line = '{"type":"result","result":"all done"}\n'
        session._process_stdout(line)
        assert session.final_result == "all done"

    def test_process_stdout_handles_non_json(self):
        session = self._make_session()
        session._process_stdout("some plain text\n")
        assert "some plain text" in session.stdout_lines

    def test_process_stdout_skips_empty_lines(self):
        session = self._make_session()
        session._process_stdout("")
        assert session.stdout_lines == []

    def test_event_handler_on_thinking_called(self):
        handler = MagicMock()
        session = self._make_session(event_handler=handler)
        line = '{"type":"assistant","message":{"content":[{"type":"text","text":"thinking..."}]}}\n'
        session._process_stdout(line)
        handler.on_thinking.assert_called_once_with("thinking...")

    def test_event_handler_on_tool_call_called(self):
        handler = MagicMock()
        session = self._make_session(event_handler=handler)
        line = (
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","name":"Read","id":"t2","input":{"path":"/tmp"}}]}}\n'
        )
        session._process_stdout(line)
        handler.on_tool_call.assert_called_once_with("Read", {"path": "/tmp"})

    def test_create_session_returns_claude_session(self, agent):
        session = agent._create_session(cmd=["claude", "-p"])
        assert isinstance(session, ClaudeGenerationSession)
