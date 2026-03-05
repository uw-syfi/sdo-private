import pytest
from pathlib import Path
from typing import Any

from app_operator.cli_agent.operator import AppOperator
from app_operator.config import AgentConfig, Config
from libs.agent_cli.base import CodingAgent

# --- Fake Agent ---
# Note: FakeCodingAgent is intentionally NOT replaced by the shared StubAgent
# because it performs real filesystem I/O (writing scripts to disk) to exercise
# the full operator integration flow end-to-end.


class FakeCodingAgent(CodingAgent):
    """
    A fake agent that simulates LLM behavior for integration tests.
    It can write files to disk (simulating tool use) and return canned responses.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.calls: list[dict[str, Any]] = []

        # Behavior flags
        self.should_generate_broken_deploy = False
        self.has_fixed_deploy = False

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        self.calls.append({"prompt": prompt, "cwd": cwd, "timeout": timeout})

        # 1. Handle Script Generation
        if "Generate a comprehensive deploy.sh bash script" in prompt:
            return self._handle_deploy_generation()

        if "Generate a comprehensive health_check.sh bash script" in prompt:
            return self._handle_health_generation()

        # 2. Handle Fix Requests
        if "fix the deployment scripts" in prompt or "analyze the error" in prompt:
            return self._handle_fix()

        # 3. Handle Monitoring Analysis
        if "analyze the following health check results" in prompt:
            return self._handle_analysis()

        return "I am a fake agent."

    def _handle_deploy_generation(self) -> str:
        sds_dir = self.repo_path / ".sds"
        sds_dir.mkdir(parents=True, exist_ok=True)
        deploy_script = sds_dir / "deploy.sh"

        if self.should_generate_broken_deploy and not self.has_fixed_deploy:
            # Generate a script that fails
            content = """#!/bin/bash
echo "Starting deployment..."
echo "Oh no, a critical error!" >&2
exit 1
"""
        else:
            # Generate a working script
            content = """#!/bin/bash
COMMAND=$1
case $COMMAND in
  start)
    echo "Starting fake app..."
    # Create a marker file to prove we ran
    touch app_running.pid
    exit 0
    ;;
  stop)
    echo "Stopping fake app..."
    rm -f app_running.pid
    exit 0
    ;;
  *)
    echo "Unknown command"
    exit 1
    ;;
esac
"""

        deploy_script.write_text(content)
        deploy_script.chmod(0o755)
        return "I have generated .sds/deploy.sh"

    def _handle_health_generation(self) -> str:
        sds_dir = self.repo_path / ".sds"
        sds_dir.mkdir(parents=True, exist_ok=True)
        script = sds_dir / "health_check.sh"

        content = """#!/bin/bash

if [ -f "app_running.pid" ]; then
  echo "App is running"
  exit 0
else
  echo "App is NOT running"
  exit 1
fi
"""
        script.write_text(content)
        script.chmod(0o755)
        return "I have generated .sds/health_check.sh"

    def _handle_fix(self) -> str:
        # Simulate fixing the script by overwriting it with the good version
        self.has_fixed_deploy = True
        self._handle_deploy_generation()

        return """<summary>
1) Issue: The deployment script had a simulated bug.
2) Fix: I rewrote the script to be valid.
</summary>
I have analyzed the logs and fixed the deployment script."""

    def _handle_analysis(self) -> str:
        return """<exec_summary>System is healthy.</exec_summary>
The system appears to be running smoothly.
"""

# --- Tests ---


@pytest.fixture
def temp_repo(tmp_path):
    """Creates a temporary repository structure."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    return repo


def test_cold_start_success(temp_repo):
    """
    Scenario: Repository has no scripts.
    Expectation:
    1. Operator detects missing scripts.
    2. Agent generates them.
    3. Deployment succeeds (start).
    4. Health check passes.
    5. Cleanup succeeds (stop).
    """
    agent = FakeCodingAgent(temp_repo)
    operator = AppOperator(
        repo_path=str(temp_repo),
        agent=agent,
        health_check_max_count=1,  # Run monitoring once
        health_check_interval=0,  # Fast execution
        max_deployment_attempts=1,
        config=Config(agent=AgentConfig(provider="codex", model="test-model")),
    )

    # Run the operator
    exit_code = operator.run()

    assert exit_code == 0

    # Verify scripts were created
    assert (temp_repo / ".sds" / "deploy.sh").exists()
    assert (temp_repo / ".sds" / "health_check.sh").exists()

    # Verify cleanup happened (marker file should be gone if stop works,
    # but wait... run() calls _cleanup() in finally block?
    # Let's check the logs or the state.
    # Actually, our fake deploy.sh creates 'app_running.pid' on start and removes it on stop.
    # So if cleanup ran, the pid file should be gone.
    assert not (temp_repo / "app_running.pid").exists()


def test_deployment_fix_loop(temp_repo):
    """
    Scenario: Agent generates a broken script initially.
    Expectation:
    1. Deployment attempt 1 fails.
    2. Operator asks agent to fix.
    3. Agent 'fixes' it (writes good script).
    4. Deployment attempt 2 succeeds.
    """
    agent = FakeCodingAgent(temp_repo)
    agent.should_generate_broken_deploy = True

    operator = AppOperator(
        repo_path=str(temp_repo),
        agent=agent,
        health_check_max_count=1,
        health_check_interval=0,  # Fast execution
        max_deployment_attempts=3,
        config=Config(agent=AgentConfig(provider="codex", model="test-model")),
    )

    exit_code = operator.run()

    assert exit_code == 0
    assert agent.has_fixed_deploy is True

    # Verify we had to retry.
    # We can check the logs directory for multiple attempt logs.
    logs_dir = temp_repo / ".sds" / "logs"
    assert (logs_dir / "deploy_attempt_1.log").exists()
    assert (logs_dir / "deploy_attempt_2.log").exists()

    # Verify cleanup
    assert not (temp_repo / "app_running.pid").exists()


def test_monitoring_execution(temp_repo):
    """
    Scenario: Scripts exist. Run monitoring loop multiple times.
    """
    # Pre-create scripts
    agent = FakeCodingAgent(temp_repo)
    agent._handle_deploy_generation()
    agent._handle_health_generation()

    operator = AppOperator(
        repo_path=str(temp_repo),
        agent=agent,
        health_check_interval=0,  # fast as possible
        health_check_max_count=3,
        max_deployment_attempts=1,
        config=Config(agent=AgentConfig(provider="codex", model="test-model")),
    )

    exit_code = operator.run()

    assert exit_code == 0

    # Check that we have 3 monitoring logs
    monitor_logs = list((temp_repo / ".sds" / "logs" / "monitor").glob("check_*.log"))
    assert len(monitor_logs) == 3
