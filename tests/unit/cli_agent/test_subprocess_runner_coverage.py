import pytest
import subprocess
from unittest.mock import MagicMock, patch
from app_operator.subprocess_runner import SubprocessRunner


class MockProcess:
    def __init__(
        self, returncode=0, stdout_lines=None, stderr_lines=None, duration=0.1
    ):
        self.returncode = returncode
        self.stdout_lines = stdout_lines or []
        self.stderr_lines = stderr_lines or []
        self.duration = duration
        self.start_time = None
        self.stdout = MagicMock()
        self.stderr = MagicMock()

        # Configure pipes to return lines then empty string
        self.stdout.readline.side_effect = self.stdout_lines + [""]
        self.stderr.readline.side_effect = self.stderr_lines + [""]

        # Tracking calls
        self.terminate_called = False
        self.kill_called = False
        self.wait_called = False

    def poll(self):
        # Return None initially, then returncode after duration
        if self.start_time is None:
            self.start_time = 0  # Will be set by test controller usually
            return None
        return self.returncode  # For simplicity in this mock structure

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        self.wait_called = True
        if timeout and timeout < 0.1:  # Simulate timeout
            raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
        return self.returncode


@pytest.fixture
def runner_setup():
    cmd = ["echo", "test"]
    cwd = "/tmp"
    timeout = 10
    return cmd, cwd, timeout


def test_runner_timeout(runner_setup):
    """Test process timeout handling."""
    cmd, cwd, timeout = runner_setup

    # Custom time function to simulate passage of time
    current_time = [0.0]

    def mock_time():
        return current_time[0]

    def mock_sleep(seconds):
        current_time[0] += seconds

    # Mock process that never finishes (poll returns None always) until we kill it
    mock_proc = MockProcess()
    mock_proc.poll = MagicMock(return_value=None)

    # We need poll to return None until timeout is exceeded.
    # Logic in _wait_for_completion:
    # while process.poll() is None:
    #   if elapsed > timeout:
    #     terminate...

    runner = SubprocessRunner(
        command=cmd,
        cwd=cwd,
        timeout=timeout,
        time_func=mock_time,
        sleep_func=mock_sleep,
    )

    # Inject our mock process
    runner.popen_func = MagicMock(return_value=mock_proc)

    result = runner.run()

    # Verification
    assert result["success"] is False
    assert "timed out" in result["stderr"]
    assert mock_proc.terminate_called


def test_runner_shutdown(runner_setup):
    """Test process shutdown request handling."""
    cmd, cwd, timeout = runner_setup

    # Check shutdown returns True immediately
    check_shutdown = MagicMock(return_value=True)

    mock_proc = MockProcess()
    mock_proc.poll = MagicMock(return_value=None)

    runner = SubprocessRunner(
        command=cmd, cwd=cwd, timeout=timeout, check_shutdown=check_shutdown
    )

    runner.popen_func = MagicMock(return_value=mock_proc)

    result = runner.run()

    assert result["success"] is False
    assert "interrupted by shutdown request" in result["stderr"]
    assert mock_proc.terminate_called


def test_ensure_process_terminated_fallback(runner_setup):
    """Test fallback from terminate to kill in _ensure_process_terminated."""
    cmd, cwd, timeout = runner_setup

    mock_proc = MockProcess()
    # poll returns None (running)
    mock_proc.poll = MagicMock(return_value=None)

    # terminate raises exception or wait timeouts
    mock_proc.terminate = MagicMock()
    mock_proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd, 1))

    runner = SubprocessRunner(cmd, cwd, timeout)
    runner.process = mock_proc

    runner._ensure_process_terminated()

    assert mock_proc.terminate.called
    assert mock_proc.kill_called


def test_wait_for_completion_terminate_fails_then_kill(runner_setup):
    """Test that if terminate fails during timeout handling, kill is called."""
    cmd, cwd, timeout = runner_setup

    current_time = [0.0]

    def mock_time():
        return current_time[0]

    def mock_sleep(seconds):
        current_time[0] += seconds

    runner = SubprocessRunner(
        command=cmd,
        cwd=cwd,
        timeout=timeout,
        time_func=mock_time,
        sleep_func=mock_sleep,
    )

    mock_proc = MockProcess()
    mock_proc.poll = MagicMock(return_value=None)

    # wait raises TimeoutExpired when called after terminate
    mock_proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd, 1))

    runner.popen_func = MagicMock(return_value=mock_proc)

    # Force timeout
    # 1. run() start_time_mono
    # 2. _wait_for_completion() start_time
    # 3. loop check current_time
    runner.time_func = MagicMock(side_effect=[0, 0, timeout + 10])
    runner.sleep_func = MagicMock()

    result = runner.run()

    assert mock_proc.terminate_called
    assert mock_proc.kill_called
    assert "timed out" in result["stderr"]


def test_runner_progress_monitoring(runner_setup):
    """Test run_with_progress_monitoring."""
    cmd, cwd, timeout = runner_setup

    mock_summarizer = MagicMock()
    # should_summarize returns True once then False
    mock_summarizer.should_summarize.side_effect = [True, False, False, False, False]

    mock_proc = MockProcess()
    mock_proc.poll = MagicMock(
        side_effect=[None, None, 0, 0]
    )  # Runs for 2 loops then finishes, plus cleanup check

    runner = SubprocessRunner(cmd, cwd, timeout)
    runner.popen_func = MagicMock(return_value=mock_proc)
    runner.sleep_func = MagicMock()

    runner.run_with_progress_monitoring(mock_summarizer)

    assert mock_summarizer.start.called
    assert mock_summarizer.summarize.called


def test_log_file_open_failure(runner_setup, tmp_path):
    """Test failure to open log file."""
    cmd, cwd, timeout = runner_setup
    log_file = tmp_path / "log.txt"

    runner = SubprocessRunner(cmd, cwd, timeout, log_file_path=log_file)
    runner.popen_func = MagicMock(return_value=MockProcess())

    with patch("builtins.open", side_effect=OSError("Fail")):
        # Should not crash
        result = runner.run()
        assert result["success"]
