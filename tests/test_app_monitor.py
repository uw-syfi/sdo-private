import pytest
from app_operator.agents.app_monitor import AppMonitor, HealthCheckTask
import app_operator.agents.app_monitor as app_monitor_module


class StubAgent:
    def __init__(self, response: str = "ok"):
        self.response = response
        self.calls = []

    def generate(self, prompt: str, cwd: str | None = None,
                 timeout: int = 120) -> str:
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
    mock_result = {
        "exit_code": 0,
        "success": True,
        "stdout": "ok",
        "stderr": ""}

    calls = {"count": 0}

    def mock_run_health_check(repo, script, timeout=120):
        calls["count"] += 1
        return mock_result

    monkeypatch.setattr(
        app_monitor_module,
        "run_health_check",
        mock_run_health_check)

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


def test_analyze_health_calls_agent(monitor, stub_agent):
    health_result = {
        "exit_code": 0,
        "success": True,
        "stdout": "all good",
        "stderr": ""}
    task = HealthCheckTask()
    monitor.check_count = 1
    task.analyze(monitor, health_result)

    assert len(stub_agent.calls) == 1
    prompt, cwd, timeout = stub_agent.calls[0]
    assert "all good" in prompt
    assert cwd == str(monitor.repo_path)
    assert timeout == 120
