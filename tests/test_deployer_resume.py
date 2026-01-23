from types import MethodType
import pytest

from app_operator.agents.deployer import DeploymentAgent, DEFAULT_DEPLOY_TIMEOUT_SECS


class StubAgent:
    """Lightweight coding agent stub used by tests."""

    def __init__(self, response: str = "ok"):
        self.response = response

    def generate(self, prompt: str, cwd=None, timeout=300, silent=False) -> str:
        return self.response


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
    return DeploymentAgent(repo_path, StubAgent())


def _bind_method(obj, name, func):
    setattr(obj, name, MethodType(func, obj))


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
        self, command="start", timeout=DEFAULT_DEPLOY_TIMEOUT_SECS, log_file_path=None
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

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    # Run for 2 more attempts
    agent.run(max_attempts=2)

    # Should run attempt 3 and 4
    assert executed_attempts == [3, 4]


def test_run_starts_fresh_without_logs(agent, monkeypatch):
    executed_attempts = []

    def fake_run_deploy(
        self, command="start", timeout=DEFAULT_DEPLOY_TIMEOUT_SECS, log_file_path=None
    ):
        if log_file_path:
            name = log_file_path.stem
            parts = name.split("_")
            if len(parts) >= 3:
                executed_attempts.append(int(parts[-1]))
        return {"success": False, "exit_code": 1, "stdout": "", "stderr": ""}

    def fake_fix(self, *args, **kwargs):
        return True

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    agent.run(max_attempts=2)

    assert executed_attempts == [1, 2]
