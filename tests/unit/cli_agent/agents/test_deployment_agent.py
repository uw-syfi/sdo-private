from unittest.mock import MagicMock

import pytest

import app_operator.cli_agent.agents.deployer as deployer_module
from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.prompts.deployer import (
    create_fix_prompt,
    prepare_error_context,
)
from tests.fixtures import bind_method
from tests.fixtures.agents import TrackingAgent

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
        directory,
        agent,
        filesystem=None,
        deployment_config=None,
        operator_config=None,
        recorder=None,
        dspy_config=None,
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
    bind_method(agent, "run_deploy_command", fake_run_deploy)

    assert agent.run(max_attempts=1) is True
    assert generated["args"] == (str(repo), stub_agent)


def test_run_fails_if_script_generation_fails(tmp_path, stub_agent, monkeypatch):
    """Test deployment fails when script generation fails.

    Verifies that when generate_scripts returns False, the deployment
    agent immediately returns False without attempting deployment.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = DeploymentAgent(repo, stub_agent)

    def fake_generate_scripts(
        directory,
        agent,
        filesystem=None,
        deployment_config=None,
        operator_config=None,
        recorder=None,
        dspy_config=None,
    ):
        return False, "boom"

    monkeypatch.setattr(deployer_module, "generate_scripts", fake_generate_scripts)

    assert agent.run() is False


def test_run_succeeds_without_fix(agent, monkeypatch):
    """Test successful deployment without needing any fixes.

    Verifies that when both deployment and health check succeed on first attempt,
    the agent does not invoke any fix attempts.
    """
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

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    bind_method(agent, "_fix_with_agent", unexpected_fix)

    assert agent.run(max_attempts=1) is True


def test_run_retries_after_failure(agent, monkeypatch):
    """Test deployment retry mechanism after initial failure.

    Verifies that when deployment fails on first attempt, the agent invokes
    a fix and successfully deploys on the second attempt.
    """
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
        self,
        deploy_result,
        health_result,
        attempt,
        max_attempts,
        log_file_path=None,
        health_check_log_path=None,
    ):
        fix_calls.append((attempt, max_attempts, deploy_result["exit_code"]))
        return True

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=3) is True
    assert fix_calls == [(1, 3, 1)]


def test_run_respects_max_attempts(agent):
    """Test that deployment respects the max_attempts limit.

    Verifies that when deployment fails and max_attempts is 1, the agent
    only attempts once and does not retry.
    """
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

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=1) is False
    assert fix_calls["count"] == 1


def test_run_deploy_command_handles_subprocess_results(agent, monkeypatch):
    """Test subprocess execution and output capture.

    Verifies that run_deploy_command correctly captures stdout and stderr
    from the deployment subprocess.
    """
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
    """Test timeout handling during deployment.

    Verifies that when a deployment command times out, the agent properly
    sets exit_code to -1 and includes timeout message in stderr.
    """

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

    monkeypatch.setattr(deployer_module.subprocess, "Popen", lambda *args, **kwargs: MockProcess())

    # Mock time to simulate timeout
    # Initial call: start_time
    # Loop calls: current_time
    # We want current_time - start_time > timeout (DEFAULT_DEPLOY_TIMEOUT_SECS)

    # Start, check 1 (timeout), check 2...
    times = [
        0,
        0,  # SubprocessRunner constructor start_time
        DEFAULT_DEPLOY_TIMEOUT_SECS + 1,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 2,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 3,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 4,
        DEFAULT_DEPLOY_TIMEOUT_SECS + 5,
    ]
    monkeypatch.setattr(deployer_module.time, "time", lambda: times.pop(0))
    monkeypatch.setattr(deployer_module.time, "sleep", lambda x: None)

    result = agent.run_deploy_command("start", timeout=DEFAULT_DEPLOY_TIMEOUT_SECS)
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]


def test_deployment_skips_fix_when_max_attempts_reached(tmp_path, stub_agent):
    """Test that deployment doesn't call agent when max attempts reached."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()

    # Create failing deploy script
    deploy_script = sds_dir / "deploy.sh"
    deploy_script.write_text("#!/bin/bash\nexit 1\n")
    deploy_script.chmod(0o755)

    # Create health check script
    health_script = sds_dir / "health_check.sh"
    health_script.write_text("#!/bin/bash\nexit 0\n")
    health_script.chmod(0o755)

    agent = DeploymentAgent(repo, stub_agent)

    # Run with max_attempts=1, should not call agent for fix
    result = agent.run(max_attempts=1, check_shutdown=lambda: False)

    assert result is False
    # Agent should not be called since we're at max attempts (scripts already exist)
    assert len(stub_agent.calls) == 0


def test_deployment_calls_agent_on_failure_when_under_max_attempts(tmp_path, tracking_agent):
    """Test that deployment calls agent to fix errors when under max attempts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()

    # Create failing deploy script
    deploy_script = sds_dir / "deploy.sh"
    deploy_script.write_text("#!/bin/bash\nexit 1\n")
    deploy_script.chmod(0o755)

    # Create health check script
    health_script = sds_dir / "health_check.sh"
    health_script.write_text("#!/bin/bash\nexit 0\n")
    health_script.chmod(0o755)

    agent = DeploymentAgent(repo, tracking_agent)

    # Run with max_attempts=3, agent should be called to fix
    _ = agent.run(max_attempts=3, check_shutdown=lambda: False)

    # Agent should be called at least once to try to fix the error
    assert tracking_agent.generation_count > 0
    # Should pass the repo path as cwd
    call = tracking_agent.calls[0]
    assert call["kwargs"].get("cwd") == str(repo)


def test_deployment_handles_agent_errors_gracefully(tmp_path, error_agent):
    """Test that deployment handles agent errors without crashing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()

    # Create failing deploy script
    deploy_script = sds_dir / "deploy.sh"
    deploy_script.write_text("#!/bin/bash\nexit 1\n")
    deploy_script.chmod(0o755)

    # Create health check script
    health_script = sds_dir / "health_check.sh"
    health_script.write_text("#!/bin/bash\nexit 0\n")
    health_script.chmod(0o755)

    agent = DeploymentAgent(repo, error_agent)

    # Run should handle agent errors gracefully
    result = agent.run(max_attempts=2, check_shutdown=lambda: False)

    # Should fail but not crash
    assert result is False


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

    # Call the standalone function
    context = prepare_error_context(deploy_result, health_result)

    # The current implementation only includes basic status info, not stdout/stderr
    assert "## Deployment Script Result" in context
    assert "Exit Code: 1" in context
    assert "Status: FAILED" in context


def test_create_fix_prompt_includes_repo_and_scripts(agent):
    context = "error context"
    # Call the standalone function
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


def test_run_health_recheck_after_fix_succeeds(agent, monkeypatch):
    """Agent fixes health_check.sh → recheck passes → no full re-deploy needed."""
    health_call_count = {"n": 0}
    deploy_call_count = {"n": 0}

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        deploy_call_count["n"] += 1
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    def fake_run_health(repo, script, timeout=120, log_file_path=None):
        health_call_count["n"] += 1
        if health_call_count["n"] == 1:
            return {
                "success": False,
                "exit_code": 1,
                "stdout": "8 failed",
                "stderr": "",
            }
        # Post-fix recheck passes
        return {"success": True, "exit_code": 0, "stdout": "healthy", "stderr": ""}

    def fake_fix(
        self,
        deploy_result,
        health_result,
        attempt,
        max_attempts,
        log_file_path=None,
        health_check_log_path=None,
    ):
        return True

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=3) is True
    # Only one deploy; health called twice (initial + recheck)
    assert deploy_call_count["n"] == 1
    assert health_call_count["n"] == 2


def test_run_health_recheck_after_fix_still_fails_retries(agent, monkeypatch):
    """Recheck after agent fix still fails → falls through to full re-deploy."""
    deploy_call_count = {"n": 0}
    health_call_count = {"n": 0}

    def fake_run_deploy(
        self,
        command="start",
        timeout=DEFAULT_DEPLOY_TIMEOUT_SECS,
        log_file_path=None,
        **kwargs,
    ):
        deploy_call_count["n"] += 1
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    def fake_run_health(repo, script, timeout=120, log_file_path=None):
        health_call_count["n"] += 1
        if health_call_count["n"] <= 2:
            # Attempt-1 initial + recheck both fail
            return {"success": False, "exit_code": 1, "stdout": "broken", "stderr": ""}
        # Attempt-2 initial health check passes
        return {"success": True, "exit_code": 0, "stdout": "healthy", "stderr": ""}

    def fake_fix(
        self,
        deploy_result,
        health_result,
        attempt,
        max_attempts,
        log_file_path=None,
        health_check_log_path=None,
    ):
        return True

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)
    bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=3) is True
    # Two deploys (attempt 1 + retry attempt 2)
    assert deploy_call_count["n"] == 2
    # Three health checks: attempt1-initial, attempt1-recheck, attempt2-initial
    assert health_call_count["n"] == 3


def test_exit_code_zero_recorded_correctly(repo_path, stub_agent, monkeypatch):
    """Regression: 'int(ec or -1)' coerces 0 to -1.  Verify 0 is recorded as 0."""
    recorded_exit_codes = []

    class RecordingRecorder:
        """Minimal recorder that captures exit_codes passed to add_tool_call."""

        def start_phase(self, phase, context=None):
            pass

        def end_phase(self, status=None):
            pass

        def add_user_message(self, content):
            pass

        def add_assistant_message(self, content, duration=None):
            pass

        def add_tool_call(self, tool, args, stdout="", stderr="", exit_code=None, duration=None):
            recorded_exit_codes.append(exit_code)

        def set_phase_status(self, status):
            pass

        def set_agent_name(self, name):
            pass

        def set_prompt_version(self, version):
            pass

        def record_fallback(self):
            pass

        def record_prompt_kwargs(self, kwargs):
            pass

        def record_rendered_prompt(self, rendered):
            pass

        def finalize(self, status="completed"):
            from pathlib import Path

            return Path("/dev/null")

        @property
        def phase(self):
            from contextlib import contextmanager

            @contextmanager
            def _phase(phase, context=None):
                self.start_phase(phase, context)
                yield self
                self.end_phase()

            return _phase

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

    recorder = RecordingRecorder()
    agent = DeploymentAgent(repo_path, stub_agent, recorder=recorder)

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    monkeypatch.setattr(deployer_module, "run_health_check", fake_run_health)

    assert agent.run(max_attempts=1) is True
    # Both deploy and health_check exit_code=0 must be recorded as 0, not -1
    assert recorded_exit_codes == [0, 0]


def test_run_retries_if_fix_fails(agent):
    # This test verifies that if _fix_with_agent returns False (e.g. agent timeout/error),
    # the deployment loop continues until max attempts are reached.

    # We simulate a failure on all attempts, and _fix_with_agent returning False.
    deploy_results = iter(
        [
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
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

    bind_method(agent, "run_deploy_command", fake_run_deploy)
    bind_method(agent, "_fix_with_agent", fake_fix)

    # Run with max_attempts=3.
    # It should retry 3 times.
    assert agent.run(max_attempts=3) is False

    # Verify we tried 3 times
    assert fix_calls["count"] == 3
    assert deploy_calls["count"] == 3
