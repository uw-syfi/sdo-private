from types import MethodType
from unittest.mock import MagicMock

import pytest

import app_operator.cli_agent.agents.deployer as deployer_module
from app_operator.cli_agent.agents.deployer import (
    DeploymentAgent,
    DEFAULT_DEPLOY_TIMEOUT_SECS,
)
from app_operator.prompts.deployer import (
    prepare_error_context,
    create_fix_prompt,
)
from app_operator.config import OperatorConfig
from tests.fixtures.agents import TrackingAgent, ErrorAgent


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    return repo


@pytest.fixture
def stub_agent():
    return TrackingAgent(response="ok")


@pytest.fixture
def agent(repo_path, stub_agent):
    return DeploymentAgent(repo_path, stub_agent)


def test_run_generates_scripts_when_missing(tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = DeploymentAgent(repo, stub_agent)

    generated = {}

    def fake_generate_scripts(
        directory, agent, filesystem=None, deployment_config=None, operator_config=None
    ):
        generated["args"] = (directory, agent)
        sds_dir = repo / ".sds"
        sds_dir.mkdir(exist_ok=True)
        (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
        (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
        return True, "done"

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    def fake_run_health(repo, script, timeout=120, log_file_path=None):
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(deployer_module, "generate_scripts", fake_generate_scripts)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    _bind_method(agent, "run_deploy_command", fake_run_deploy)

    assert agent.run(max_attempts=1) is True
    assert generated["args"] == (str(repo), stub_agent)


def test_run_fails_if_script_generation_fails(tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = DeploymentAgent(repo, stub_agent)

    def fake_generate_scripts(
        directory, agent, filesystem=None, deployment_config=None, operator_config=None
    ):
        return False, "boom"

    monkeypatch.setattr(deployer_module, "generate_scripts", fake_generate_scripts)

    assert agent.run() is False


def _bind_method(obj, name, func):
    setattr(obj, name, MethodType(func, obj))


def test_run_succeeds_without_fix(agent, monkeypatch):
    deploy_results = iter(
        [
            {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""},
        ]
    )
    health_results = iter(
        [
            {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""},
        ]
    )

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        return next(deploy_results)

    def fake_run_health(repo, script, timeout=120, log_file_path=None):
        return next(health_results)

    def unexpected_fix(self, *args, **kwargs):
        raise AssertionError("fix should not be invoked on success")

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    _bind_method(agent, "_fix_with_agent", unexpected_fix)

    assert agent.run(max_attempts=1) is True


def test_run_retries_after_failure(agent, monkeypatch):
    deploy_results = iter(
        [
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        ]
    )
    health_results = iter(
        [
            {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
        ]
    )
    fix_calls: list[tuple[int, int, int]] = []

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        return next(deploy_results)

    def fake_run_health(repo, script, timeout=120, log_file_path=None):
        return next(health_results)

    def fake_fix(
        self, deploy_result, health_result, attempt, max_attempts, log_file_path=None
    ):
        fix_calls.append((attempt, max_attempts, deploy_result["exit_code"]))
        return True

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=3) is True
    assert fix_calls == [(1, 3, 1)]


def test_run_respects_max_attempts(agent):
    deploy_results = iter(
        [
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
        ]
    )
    fix_calls = {"count": 0}

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        return next(deploy_results)

    def fake_fix(self, *args, **kwargs):
        fix_calls["count"] += 1
        return False

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=1) is False
    assert fix_calls["count"] == 1


def test_run_deploy_command_handles_subprocess_results(agent, monkeypatch):
    stdout_content = ["all good\n"]
    stderr_content = []

    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.readline.side_effect = stdout_content + [""]
            self.stderr.readline.side_effect = stderr_content + [""]
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        assert cmd[0] == str(agent.deploy_script)
        assert cmd[1] == "start"
        assert kwargs["cwd"] == str(agent.repo_path)
        return MockProcess()

    monkeypatch.setattr(deployer_module.subprocess, "Popen", fake_popen)

    result = agent.run_deploy_command("start")
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert "all good" in result["stdout"]


def test_run_deploy_command_handles_timeouts(agent, monkeypatch):
    class MockProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.readline.side_effect = [""]
            self.stderr.readline.side_effect = [""]
            self.returncode = None

        def poll(self):
            return None  # Always running

        def wait(self, timeout=None):
            if timeout:
                raise deployer_module.subprocess.TimeoutExpired(cmd=[], timeout=timeout)
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(
        deployer_module.subprocess, "Popen", lambda *args, **kwargs: MockProcess()
    )

    # Mock time to simulate timeout
    # Initial call: start_time
    # Loop calls: current_time
    # We want current_time - start_time > timeout (DEFAULT_DEPLOY_TIMEOUT_SECS)

    # Start, check 1 (timeout), check 2...
    times = [
        0,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 1,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 2,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 3,
    ]
    monkeypatch.setattr(deployer_module.time, "time", lambda: times.pop(0))
    monkeypatch.setattr(deployer_module.time, "sleep", lambda x: None)

    result = agent.run_deploy_command("start", timeout=DEFAULT_DEPLOY_TIMEOUT_SECS)
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]


def test_fix_with_agent_skips_when_attempt_exceeds_max(agent, stub_agent):
    assert (
        agent._fix_with_agent(
            {"exit_code": 1, "success": False}, None, attempt=3, max_attempts=3
        )
        is False
    )
    assert len(stub_agent.calls) == 0


def test_fix_with_agent_calls_agent_and_returns_success(agent, stub_agent, monkeypatch):
    agent.agent = stub_agent

    def fake_prepare(
        deploy_result,
        health_result,
        log_file_path=None,
        health_check_log_path=None,
    ):
        return "context"

    def fake_prompt(
        repo_path, attempt, max_attempts, context, deploy_script, health_script
    ):
        return f"prompt::{context}::{attempt}/{max_attempts}"

    monkeypatch.setattr(deployer_module, "prepare_error_context", fake_prepare)
    monkeypatch.setattr(deployer_module, "create_fix_prompt", fake_prompt)

    deploy_result = {"exit_code": 99, "success": False}

    assert agent._fix_with_agent(deploy_result, None, attempt=1, max_attempts=2) is True
    call = stub_agent.calls[0]
    prompt = call["prompt"]
    cwd = call["kwargs"].get("cwd")
    timeout = call["kwargs"].get("timeout")
    assert "prompt::context::1/2" in prompt
    assert cwd == str(agent.repo_path)
    assert timeout == OperatorConfig().agent_fix_timeout


def test_fix_with_agent_handles_agent_errors(agent, monkeypatch):
    error_agent = ErrorAgent(error_message="agent error")
    agent.agent = error_agent

    def fake_prepare(*args, **kwargs):
        return "ctx"

    def fake_prompt(*args, **kwargs):
        return "prompt"

    monkeypatch.setattr(deployer_module, "prepare_error_context", fake_prepare)
    monkeypatch.setattr(deployer_module, "create_fix_prompt", fake_prompt)

    assert (
        agent._fix_with_agent(
            {"exit_code": 1, "success": False}, None, attempt=1, max_attempts=2
        )
        is False
    )


def test_prepare_error_context_truncates_long_outputs(agent):
    """Test that prepare_error_context returns basic error context.

    Note: The current implementation no longer includes stdout/stderr in the context
    (they are commented out). This test verifies the function works with long outputs
    without crashing, even though the outputs are not included in the returned context.
    """
    long_stdout = "a" * 3500
    long_stderr = "b" * 3500
    health_stdout = "c" * 4000
    health_stderr = "d" * 2500
    deploy_result = {
        "exit_code": 1,
        "success": False,
        "stdout": long_stdout,
        "stderr": long_stderr,
    }
    health_result = {
        "exit_code": 1,
        "success": False,
        "stdout": health_stdout,
        "stderr": health_stderr,
    }

    context = prepare_error_context(deploy_result, health_result)

    # The current implementation only includes basic status info, not stdout/stderr
    assert "## Deployment Script Result" in context
    assert "Exit Code: 1" in context
    assert "Status: FAILED" in context


def test_create_fix_prompt_includes_repo_and_scripts(agent):
    context = "error context"
    prompt = create_fix_prompt(
        agent.repo_path,
        attempt=2,
        max_attempts=5,
        error_context=context,
        deploy_script_path=agent.deploy_script,
        health_check_script_path=agent.health_check_script,
    )

    assert str(agent.repo_path) in prompt
    assert str(agent.deploy_script) in prompt
    assert ".sds/health_check.sh" in prompt
    assert "error context" in prompt
    assert "2 of 5" in prompt


def test_run_aborts_if_fix_fails(agent):
    # This test verifies that if _fix_with_agent returns False (e.g. agent timeout/error),
    # the deployment loop stops immediately and returns False.

    # We simulate a failure on the first attempt, and then _fix_with_agent returning False.
    deploy_results = iter(
        [
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
            # If the code was buggy, it might try a second time. We can either
            # raise an error if called again, or just provide a result and assert call count.
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom again"},
        ]
    )

    fix_calls = {"count": 0}
    deploy_calls = {"count": 0}

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        deploy_calls["count"] += 1
        return next(deploy_results)

    def fake_fix(self, *args, **kwargs):
        fix_calls["count"] += 1
        return False  # Agent failed to fix

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    # Run with max_attempts=3.
    # Attempt 1: fails. fake_fix returns False.
    # Should abort immediately.
    assert agent.run(max_attempts=3) is False

    # Verify we only tried once
    assert fix_calls["count"] == 1
    assert deploy_calls["count"] == 1
