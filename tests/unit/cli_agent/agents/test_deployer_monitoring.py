import itertools
from unittest.mock import MagicMock, patch

import pytest

from app_operator.cli_agent.agents.deployer import DeploymentAgent


class MockProcess:
    def __init__(self, stdout_content, stderr_content, duration_steps=5):
        self.stdout = MagicMock()
        self.stderr = MagicMock()
        self.stdout_iter = iter(stdout_content)
        self.stderr_iter = iter(stderr_content)
        self.duration_steps = duration_steps
        self.current_step = 0
        self.returncode = 0

        # Setup readline to yield lines
        def make_readline(iterator):
            def readline():
                try:
                    return next(iterator)
                except StopIteration:
                    return ""

            return readline

        self.stdout.readline.side_effect = make_readline(self.stdout_iter)
        self.stderr.readline.side_effect = make_readline(self.stderr_iter)

    def poll(self):
        if self.current_step < self.duration_steps:
            self.current_step += 1
            return None
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        pass


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    # Return a summary in XML tags
    agent.generate.return_value = "<output_msg>Summary of progress</output_msg>"
    return agent


@pytest.fixture
def deployer(tmp_path, mock_agent):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".sds").mkdir()
    return DeploymentAgent(repo, mock_agent)


def test_monitoring_summary_trigger(deployer, mock_agent):
    """Test that summary is triggered when command runs long enough."""

    # Simulate a process that produces output and runs for 3 "steps"
    # We will manipulate time to make these steps take longer than the threshold

    stdout_lines = [f"line {i}\n" for i in range(50)]
    stderr_lines = []

    # 30 steps of poll checks
    mock_process = MockProcess(stdout_lines, stderr_lines, duration_steps=30)

    # Mock subprocess.Popen
    with patch("subprocess.Popen", return_value=mock_process):
        # Use a monotonically incrementing counter for time.time so that
        # every call returns a strictly increasing value (no fragile iterator).
        base_time = 1000.0
        tick = itertools.count(1)

        def fake_time():
            return base_time + next(tick)

        def fake_sleep(seconds):
            # Consume a tick so the clock keeps moving forward
            next(tick)

        with patch("time.time", side_effect=fake_time):
            with patch("time.sleep", side_effect=fake_sleep):
                deployer.run_deploy_command("start")

    # Verification
    # Logic:
    # Initial delay: 15s
    # Summary interval: 10s
    # Total simulated duration depends on loop count.
    # If we run for 30 polls, and each poll advances time by 1s + sleep(0.5),
    # we progress roughly 1.5s per poll -> 45s total.
    # Should trigger at 15s, 25s, 35s... (approx)

    assert mock_agent.generate.called
    assert mock_agent.generate.call_count >= 1

    # Check call arguments
    call_args = mock_agent.generate.call_args
    prompt = call_args[0][0]
    kwargs = call_args[1]

    assert "silent=True" in str(kwargs) or kwargs.get("silent") is True
    assert "<output_msg>" in prompt
    assert "Brief, one-line summary" in prompt or "one-line summary" in prompt


def test_short_command_no_summary(deployer, mock_agent):
    """Test that summary is NOT triggered for short commands."""

    stdout_lines = ["done\n"]
    stderr_lines = []

    # 2 steps
    mock_process = MockProcess(stdout_lines, stderr_lines, duration_steps=2)

    with patch("subprocess.Popen", return_value=mock_process):
        # Use a monotonically incrementing counter with small step so
        # total elapsed time stays under the 15s summary threshold.
        base_time = 1000.0
        tick = itertools.count(1)

        def fake_time():
            return base_time + next(tick) * 0.1

        def fake_sleep(seconds):
            next(tick)

        with patch("time.time", side_effect=fake_time):
            with patch("time.sleep", side_effect=fake_sleep):
                deployer.run_deploy_command("start")

    # Should not have triggered summary because elapsed time < 15s
    # 2 steps * (0.1s + 0.5s) = 1.2s approx.
    assert not mock_agent.generate.called
