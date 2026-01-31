import pytest
from unittest.mock import Mock
from app_operator.cli_agent.subprocess_runner import SubprocessRunner
from app_operator.ui import OperatorUI


@pytest.fixture
def mock_ui():
    return Mock(spec=OperatorUI)


def test_run_emits_tool_events(tmp_path, mock_ui):
    # Mock popen
    mock_popen = Mock()
    mock_process = Mock()
    mock_process.returncode = 0
    mock_process.poll.return_value = 0
    # Simulate empty output
    mock_process.stdout.readline.side_effect = [""]
    mock_process.stderr.readline.side_effect = [""]
    mock_popen.return_value = mock_process

    runner = SubprocessRunner(
        command=["ls"],
        cwd=str(tmp_path),
        timeout=10,
        ui=mock_ui,
        tool_name="test_tool",
        tool_args={"arg": "val"},
        popen_func=mock_popen,
    )

    runner.run()

    mock_ui.on_tool_call.assert_called_once_with("test_tool", {"arg": "val"})
    mock_ui.on_tool_result.assert_called_once()

    # Check result args
    args = mock_ui.on_tool_result.call_args[1]
    assert args["tool"] == "test_tool"
    assert args["exit_code"] == 0


def test_run_with_progress_emits_tool_events(tmp_path, mock_ui):
    # Mock popen
    mock_popen = Mock()
    mock_process = Mock()
    mock_process.returncode = 0
    mock_process.poll.return_value = 0
    mock_process.stdout.readline.side_effect = [""]
    mock_process.stderr.readline.side_effect = [""]
    mock_popen.return_value = mock_process

    runner = SubprocessRunner(
        command=["ls"],
        cwd=str(tmp_path),
        timeout=10,
        ui=mock_ui,
        tool_name="test_tool",
        tool_args={"arg": "val"},
        popen_func=mock_popen,
    )

    mock_summarizer = Mock()
    mock_summarizer.should_summarize.return_value = False

    runner.run_with_progress_monitoring(mock_summarizer)

    mock_ui.on_tool_call.assert_called_once_with("test_tool", {"arg": "val"})
    mock_ui.on_tool_result.assert_called_once()
