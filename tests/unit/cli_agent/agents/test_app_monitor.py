import pytest

from app_operator.cli_agent.agents.app_monitor import AppMonitor, HealthCheckTask
from tests.fixtures.agents import StubAgent

HEALTHY_RESPONSE = (
    "<health_verdict>healthy</health_verdict>\n"
    "<health_assessment>All services healthy.</health_assessment>\n"
    "<diagnosis></diagnosis>\n"
    "<script_fixed>false</script_fixed>"
)


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds = repo / ".sds"
    sds.mkdir()
    hc = sds / "health_check.sh"
    hc.write_text("#!/bin/bash\nexit 0\n")
    hc.chmod(0o755)
    return repo


@pytest.fixture
def stub_agent():
    return StubAgent(response=HEALTHY_RESPONSE)


@pytest.fixture
def monitor(repo_path, stub_agent):
    return AppMonitor(repo_path, stub_agent)


def test_run_executes_health_check_task(monitor, stub_agent):
    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    monitor.run(interval=0, check_shutdown=check_shutdown)

    assert monitor.check_count == 1
    assert len(stub_agent.calls) >= 1


def test_run_respects_max_checks(monitor, stub_agent):
    monitor.run(interval=0, max_checks=2)
    assert monitor.check_count == 2


def test_run_logs_healthy_verdict(monitor, stub_agent, capture_logs):
    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    monitor.run(interval=0, check_shutdown=check_shutdown)

    assert any("healthy" in msg for msg in capture_logs)


def test_run_saves_assessment_log(monitor, stub_agent, tmp_path):
    monitor.log_dir = tmp_path / "logs"

    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    monitor.run(interval=0, check_shutdown=check_shutdown)

    log_files = list(monitor.log_dir.glob("*.log"))
    assert len(log_files) >= 1
    assert any("healthy" in f.read_text() for f in log_files)


def test_run_handles_unhealthy_verdict(monitor, tmp_path, capture_logs, monkeypatch):
    from app_operator.cli_agent.agents.health_judge import AppHealthJudge, HealthVerdict

    def fake_assess(self, max_retries=2):
        return HealthVerdict(
            healthy=False,
            assessment="MongoDB is crashing.",
            diagnosis="mongodb: OOMKill",
            script_was_fixed=False,
            raw_response="",
        )

    monkeypatch.setattr(AppHealthJudge, "assess", fake_assess)
    monitor.log_dir = tmp_path / "logs"

    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    monitor.run(interval=0, check_shutdown=check_shutdown)

    assert not monitor.healthy


def test_run_handles_agent_exception(monitor, capture_logs):
    def raise_error(*args, **kwargs):
        raise RuntimeError("Agent API failure")

    monitor.agent.generate = raise_error

    shutdown_calls = {"count": 0}

    def check_shutdown():
        shutdown_calls["count"] += 1
        return shutdown_calls["count"] > 1

    # Should not crash
    monitor.run(interval=0, check_shutdown=check_shutdown)

    assert any("Agent API failure" in msg for msg in capture_logs)


def test_analyze_is_noop(monitor):
    task = HealthCheckTask()
    # analyze() should be callable but does nothing
    task.analyze(monitor, {"exit_code": 0, "success": True, "stdout": "", "stderr": ""})
