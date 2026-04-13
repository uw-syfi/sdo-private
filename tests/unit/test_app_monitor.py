import pytest

import app_operator.cli_agent.agents.app_monitor as app_monitor_module
from app_operator.cli_agent.agents.app_monitor import AppMonitor, HealthCheckTask
from app_operator.types import CommandResult


class StubAgent:
    def __init__(self, response: str = "ok"):
        self.response = response
        self.calls = []

    def generate(self, prompt: str, cwd: str | None = None, timeout: int = 120) -> str:
        self.calls.append((prompt, cwd, timeout))
        return self.response


@pytest.fixture
def repo_path(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def stub_agent():
    return StubAgent()


@pytest.fixture
def monitor(repo_path, stub_agent):
    return AppMonitor(repo_path, stub_agent)


def test_run_executes_health_check_task(monitor, stub_agent, monkeypatch):
    # Mock run_health_check
    mock_result = {"exit_code": 0, "success": True, "stdout": "ok", "stderr": ""}

    calls = {"count": 0}

    def mock_run_health_check(repo, script, timeout=120, ui=None):
        calls["count"] += 1
        return mock_result

    monkeypatch.setattr(app_monitor_module, "run_health_check", mock_run_health_check)

    # We want run to execute loop once.
    # We can control loop by using a check_shutdown that returns False once
    # then True.
    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    monitor.run(interval=0, check_shutdown=check_shutdown)

    assert calls["count"] == 1
    assert len(stub_agent.calls) == 1
    assert "ok" in stub_agent.calls[0][0]  # prompt contains context


def test_run_respects_max_checks(monitor, monkeypatch):
    # Mock run_health_check
    mock_result = {"exit_code": 0, "success": True, "stdout": "ok", "stderr": ""}

    calls = {"count": 0}

    def mock_run_health_check(repo, script, timeout=120, ui=None):
        calls["count"] += 1
        return mock_result

    monkeypatch.setattr(app_monitor_module, "run_health_check", mock_run_health_check)

    # Run with max_checks=2
    monitor.run(interval=0, max_checks=2)

    assert calls["count"] == 2


def test_analyze_health_calls_agent(monitor, stub_agent):
    health_result = CommandResult(exit_code=0, success=True, stdout="all good", stderr="")
    task = HealthCheckTask()
    monitor.check_count = 1
    task.analyze(monitor, health_result)

    assert len(stub_agent.calls) == 1
    prompt, cwd, timeout = stub_agent.calls[0]
    assert "all good" in prompt
    assert cwd == str(monitor.repo_path)
    assert timeout == 120


def test_analyze_parses_exec_summary(monitor, stub_agent, capture_logs, tmp_path):
    # Setup valid XML response
    stub_agent.response = (
        "Here is the analysis:\n<exec_summary>System is healthy and performing well.</exec_summary>\nDetails: ..."
    )

    task = HealthCheckTask()
    monitor.check_count = 1
    monitor.log_dir = tmp_path / "logs"

    # Dummy health result
    health_result = CommandResult(exit_code=0, success=True, stdout="ok", stderr="")

    task.analyze(monitor, health_result)

    assert any("Summary: System is healthy and performing well." in msg for msg in capture_logs)

    # Verify log file creation
    log_files = list(monitor.log_dir.glob("*.log"))
    assert len(log_files) == 1
    assert "System is healthy" in log_files[0].read_text()


def test_analyze_handles_missing_summary(monitor, stub_agent, capture_logs, tmp_path):
    # Response without tags
    stub_agent.response = "Just some plain text analysis without tags."

    task = HealthCheckTask()
    monitor.check_count = 1
    monitor.log_dir = tmp_path / "logs"

    health_result = CommandResult(exit_code=0, success=True, stdout="ok", stderr="")

    task.analyze(monitor, health_result)

    assert any("Summary not found in expected XML format" in msg for msg in capture_logs)


def test_analyze_handles_agent_exception(monitor, stub_agent, capture_logs, tmp_path):
    # Mock agent to raise exception
    def raise_error(*args, **kwargs):
        raise RuntimeError("Agent API failure")

    stub_agent.generate = raise_error
    monitor.log_dir = tmp_path / "logs"

    task = HealthCheckTask()
    health_result = CommandResult(exit_code=0, success=True, stdout="", stderr="")

    # Should not crash
    task.analyze(monitor, health_result)

    assert any("Agent analysis failed: Agent API failure" in msg for msg in capture_logs)
