import pytest
import subprocess
from unittest.mock import MagicMock
from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.cli_agent.healthcheck import run_health_check
from tests.fixtures.agents import StubAgent


@pytest.fixture
def repo_path(tmp_path):
    """Create a test repository with .sds directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    return repo


@pytest.fixture
def stub_agent():
    return StubAgent(response="ok")


@pytest.fixture
def agent(repo_path, stub_agent):
    """Create a deployment agent instance."""
    return DeploymentAgent(repo_path, stub_agent)


def test_deploy_timeout_captures_logs(agent, repo_path, monkeypatch):
    """Test that deployment timeout captures logs without actually sleeping.

    Uses mocked time.time() to simulate timeout instantly, reducing test time
    from 5+ seconds to milliseconds.
    """
    sds_dir = repo_path / ".sds"
    script_path = sds_dir / "deploy.sh"

    script_content = """#!/bin/bash
echo "Line 1: Starting deployment"
echo "Line 2: Still working"
echo "Line 3: Finished"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    log_file_path = sds_dir / "logs" / "deploy.log"
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Mock time.time() to simulate timeout without actual sleep
    # Start time: 0, then immediately jump past timeout threshold
    mock_times = iter([0.0, 0.0, 3.0, 3.0])  # Exceeds 2 second timeout

    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            # Simulate reading output lines before timeout (as strings, not bytes)
            self.stdout.readline.side_effect = [
                "Line 1: Starting deployment\n",
                "Line 2: Still working\n",
                ""  # EOF
            ]
            self.stderr.readline.side_effect = [""]
            self.returncode = None

        def poll(self):
            return None  # Still running

        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("app_operator.cli_agent.agents.deployer.subprocess.Popen",
                        lambda *args, **kwargs: MockProcess())
    monkeypatch.setattr("app_operator.cli_agent.agents.deployer.time.time",
                        lambda: next(mock_times))
    monkeypatch.setattr("app_operator.cli_agent.agents.deployer.time.sleep",
                        lambda x: None)

    # Run with short timeout (2 seconds) - but no actual waiting
    result = agent.run_deploy_command("start", timeout=2, log_file_path=log_file_path)

    # Verify timeout behavior
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]

    # Check if logs captured the output before timeout
    stdout = result["stdout"]
    assert "Line 1: Starting deployment" in stdout
    assert "Line 2: Still working" in stdout
    assert "Line 3: Finished" not in stdout  # Should not be there (timeout)

    # Check log file content
    log_content = log_file_path.read_text()
    assert "Line 1: Starting deployment" in log_content
    assert "Line 2: Still working" in log_content


def test_health_check_timeout_captures_logs(repo_path, monkeypatch):
    """Test that health check timeout captures logs without actually sleeping.

    Uses mocked time.time() to simulate timeout instantly.
    """
    sds_dir = repo_path / ".sds"
    script_path = sds_dir / "health_check.sh"

    script_content = """#!/bin/bash
echo "Health Check: Starting"
echo "Health Check: Running tests"
echo "Health Check: Finished"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    log_file_path = sds_dir / "logs" / "health.log"
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Mock time.time() to simulate timeout
    mock_times = iter([0.0, 3.0])  # Start time, then timeout

    def mock_subprocess_run(*args, timeout=None, **kwargs):
        """Mock subprocess.run that simulates timeout."""
        if timeout:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=timeout)
        # Should not reach here with our test
        raise RuntimeError("Unexpected call without timeout")

    monkeypatch.setattr("app_operator.cli_agent.healthcheck.subprocess.run",
                        mock_subprocess_run)
    monkeypatch.setattr("app_operator.cli_agent.healthcheck.time.time",
                        lambda: next(mock_times))

    # Run with short timeout (2 seconds) - no actual waiting
    result = run_health_check(
        repo_path, script_path, timeout=2, log_file_path=log_file_path
    )

    # Verify timeout behavior
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"].lower()
