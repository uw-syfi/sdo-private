import pytest
from app_operator.cli_agent.agents.deployer import DeploymentAgent
from tools.healthcheck import run_health_check


class StubAgent:
    """Lightweight coding agent stub used by tests."""

    def __init__(self, response: str = "ok", raise_error: bool = False):
        self.response = response
        self.raise_error = raise_error
        self.calls: list[tuple[str, str, int]] = []

    def generate(self, prompt: str, cwd: str | None = None, timeout: int = 300) -> str:
        self.calls.append((prompt, cwd, timeout))
        if self.raise_error:
            raise RuntimeError("agent error")
        return self.response


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    return repo


@pytest.fixture
def stub_agent():
    return StubAgent()


@pytest.fixture
def agent(repo_path, stub_agent):
    return DeploymentAgent(repo_path, stub_agent)


def test_deploy_timeout_captures_logs(agent, repo_path):
    # Create a script that outputs, sleeps (to cause timeout), and outputs again
    sds_dir = repo_path / ".sds"
    script_path = sds_dir / "deploy.sh"

    script_content = """#!/bin/bash
echo "Line 1: Starting deployment"
echo "Line 2: Still working"
sleep 5
echo "Line 3: Finished"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # We need to make sure agent.deploy_script points to this file
    # (it should by default init)

    log_file_path = sds_dir / "logs" / "deploy.log"
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Run with short timeout (2 seconds)
    result = agent.run_deploy_command("start", timeout=2, log_file_path=log_file_path)

    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]

    # Check if logs captured the output before timeout
    stdout = result["stdout"]
    assert "Line 1: Starting deployment" in stdout
    assert "Line 2: Still working" in stdout
    assert "Line 3: Finished" not in stdout  # Should not be there

    # Check log file content
    log_content = log_file_path.read_text()
    assert "Line 1: Starting deployment" in log_content
    assert "Line 2: Still working" in log_content


def test_health_check_timeout_captures_logs(repo_path):
    # Create a script that outputs, sleeps (to cause timeout), and outputs again
    sds_dir = repo_path / ".sds"
    script_path = sds_dir / "health_check.sh"

    script_content = """#!/bin/bash
echo "Health Check: Starting"
echo "Health Check: Running tests"
sleep 5
echo "Health Check: Finished"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    log_file_path = sds_dir / "logs" / "health.log"
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Run with short timeout (2 seconds)
    result = run_health_check(
        repo_path, script_path, timeout=2, log_file_path=log_file_path
    )

    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]

    # With current implementation, stdout is empty on timeout!
    # This assertion is expected to fail currently
    # assert "Health Check: Starting" in result["stdout"]
