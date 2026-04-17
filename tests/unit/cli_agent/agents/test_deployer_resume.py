import pytest

from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.core import DeploymentError
from tests.fixtures import bind_method
from tests.fixtures.agents import StubAgent

# Default deploy timeout used by OperatorConfig; kept here for test readability.
DEFAULT_DEPLOY_TIMEOUT_SECS = 900


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    (sds_dir / "logs").mkdir()
    return repo


@pytest.fixture
def agent(repo_path):
    return DeploymentAgent(repo_path, StubAgent(response="ok"))


def test_get_next_attempt_number_starts_at_one(agent):
    assert agent._get_next_attempt_number() == 1


def test_get_next_attempt_number_detects_existing_logs(agent, repo_path):
    logs_dir = repo_path / ".sds" / "logs"
    (logs_dir / "deploy_attempt_1.log").write_text("log")
    (logs_dir / "deploy_attempt_2.log").write_text("log")
    (logs_dir / "deploy_attempt_4.log").write_text("log")  # Gap in numbering

    assert agent._get_next_attempt_number() == 5


def test_run_resumes_from_existing_attempts(agent, repo_path, monkeypatch):
    # Setup existing logs
    logs_dir = repo_path / ".sds" / "logs"
    (logs_dir / "deploy_attempt_1.log").write_text("log")
    (logs_dir / "deploy_attempt_2.log").write_text("log")

    # Track executed attempts
    executed_attempts = []

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        # Extract attempt number from log path
        if log_file_path:
            name = log_file_path.stem
            parts = name.split("_")
            if len(parts) >= 3:
                executed_attempts.append(int(parts[-1]))
        return {"success": False, "exit_code": 1, "stdout": "", "stderr": ""}

    def fake_fix(
        self,
        deploy_result,
        health_result,
        attempt,
        max_attempts,
        log_file_path=None,
        health_check_log_path=None,
    ):
        return True  # Continue

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    bind_method(agent, "_fix_with_agent", fake_fix)

    # Run up to absolute ceiling 4 (all attempts fail → DeploymentError)
    with pytest.raises(DeploymentError):
        agent.run(max_attempts=4)

    # Should run attempt 3 and 4
    assert executed_attempts == [3, 4]


def test_run_refuses_resume_beyond_absolute_ceiling(agent, repo_path):
    logs_dir = repo_path / ".sds" / "logs"
    (logs_dir / "deploy_attempt_1.log").write_text("log")
    (logs_dir / "deploy_attempt_2.log").write_text("log")

    executed_attempts = []

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        executed_attempts.append(log_file_path)
        return {"success": False, "exit_code": 1, "stdout": "", "stderr": ""}

    bind_method(agent, "run_deploy_command", fake_run_deploy)

    with pytest.raises(DeploymentError):
        agent.run(max_attempts=2)
    assert executed_attempts == []


def test_run_starts_fresh_without_logs(agent, monkeypatch):
    executed_attempts = []

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        if log_file_path:
            name = log_file_path.stem
            parts = name.split("_")
            if len(parts) >= 3:
                executed_attempts.append(int(parts[-1]))
        return {"success": False, "exit_code": 1, "stdout": "", "stderr": ""}

    def fake_fix(self, *args, **kwargs):
        return True

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    bind_method(agent, "_fix_with_agent", fake_fix)

    with pytest.raises(DeploymentError):
        agent.run(max_attempts=2)

    assert executed_attempts == [1, 2]
