from types import MethodType, SimpleNamespace

import pytest

import app_operator.agents.deployer as deployer_module
from app_operator.agents.deployer import DeploymentAgent


class StubAgent:
    """Lightweight coding agent stub used by tests."""

    def __init__(self, response: str = "ok", raise_error: bool = False):
        self.response = response
        self.raise_error = raise_error
        self.calls: list[tuple[str, str, int]] = []

    def generate(self, prompt: str, cwd: str | None = None,
                 timeout: int = 300) -> str:
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
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    return repo


@pytest.fixture
def stub_agent():
    return StubAgent()


@pytest.fixture
def agent(repo_path, stub_agent):
    return DeploymentAgent(repo_path, stub_agent)


def test_run_generates_scripts_when_missing(
        tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = DeploymentAgent(repo, stub_agent)

    generated = {}

    def fake_generate_scripts(directory, agent):
        generated["args"] = (directory, agent)
        sds_dir = repo / ".sds"
        sds_dir.mkdir(exist_ok=True)
        (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
        (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
        return True, "done"

    def fake_run_deploy(self, command="start", timeout=300):
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    def fake_run_health(self, timeout=120):
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(
        deployer_module,
        "generate_scripts",
        fake_generate_scripts)
    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "run_health_check", fake_run_health)

    assert agent.run(max_attempts=1) is True
    assert generated["args"] == (str(repo), stub_agent)


def test_run_fails_if_script_generation_fails(
        tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = DeploymentAgent(repo, stub_agent)

    def fake_generate_scripts(directory, agent):
        return False, "boom"

    monkeypatch.setattr(
        deployer_module,
        "generate_scripts",
        fake_generate_scripts)

    assert agent.run() is False


def _bind_method(obj, name, func):
    setattr(obj, name, MethodType(func, obj))


def test_run_succeeds_without_fix(agent):
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

    def fake_run_deploy(self, command="start", timeout=300):
        return next(deploy_results)

    def fake_run_health(self, timeout=120):
        return next(health_results)

    def unexpected_fix(self, *args, **kwargs):
        raise AssertionError("fix should not be invoked on success")

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "run_health_check", fake_run_health)
    _bind_method(agent, "_fix_with_agent", unexpected_fix)

    assert agent.run(max_attempts=1) is True


def test_run_retries_after_failure(agent):
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
    fix_calls: list[tuple[int, int]] = []

    def fake_run_deploy(self, command="start", timeout=300):
        return next(deploy_results)

    def fake_run_health(self, timeout=120):
        return next(health_results)

    def fake_fix(self, deploy_result, health_result, attempt, max_attempts):
        fix_calls.append((attempt, max_attempts, deploy_result["exit_code"]))
        return True

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "run_health_check", fake_run_health)
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

    def fake_run_deploy(self, command="start", timeout=300):
        return next(deploy_results)

    def fake_fix(self, *args, **kwargs):
        fix_calls["count"] += 1
        return False

    _bind_method(agent, "run_deploy_command", fake_run_deploy)
    _bind_method(agent, "_fix_with_agent", fake_fix)

    assert agent.run(max_attempts=1) is False
    assert fix_calls["count"] == 1


def test_run_deploy_command_handles_subprocess_results(agent, monkeypatch):
    def fake_run(cmd, cwd, capture_output, text, timeout):
        assert cmd[0] == str(agent.deploy_script)
        assert cmd[1] == "start"
        assert cwd == str(agent.repo_path)
        return SimpleNamespace(returncode=0, stdout="all good", stderr="")

    monkeypatch.setattr(deployer_module.subprocess, "run", fake_run)

    result = agent.run_deploy_command("start")
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["stdout"] == "all good"


def test_run_deploy_command_handles_timeouts(agent, monkeypatch):
    def fake_run(*_, **__):
        raise deployer_module.subprocess.TimeoutExpired(
            cmd=["./deploy.sh", "start"], timeout=300)

    monkeypatch.setattr(deployer_module.subprocess, "run", fake_run)

    result = agent.run_deploy_command("start")
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]


def test_run_health_check_maps_failures(agent, monkeypatch):
    def fake_run(*_, **__):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad")

    monkeypatch.setattr(deployer_module.subprocess, "run", fake_run)

    result = agent.run_health_check()
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert result["stderr"] == "bad"


def test_run_health_check_handles_runtime_errors(agent, monkeypatch):
    def fake_run(*_, **__):
        raise RuntimeError("boom")

    monkeypatch.setattr(deployer_module.subprocess, "run", fake_run)

    result = agent.run_health_check()
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "Failed to run health check" in result["stderr"]


def test_fix_with_agent_skips_when_attempt_exceeds_max(agent, stub_agent):
    assert agent._fix_with_agent(
        {"exit_code": 1, "success": False}, None, attempt=3, max_attempts=3) is False
    assert stub_agent.calls == []


def test_fix_with_agent_calls_agent_and_returns_success(
        agent, stub_agent, monkeypatch):
    agent.agent = stub_agent

    def fake_prepare(self, deploy_result, health_result):
        return "context"

    def fake_prompt(self, context, attempt, max_attempts):
        return f"prompt::{context}::{attempt}/{max_attempts}"

    agent._prepare_error_context = MethodType(fake_prepare, agent)
    agent._create_fix_prompt = MethodType(fake_prompt, agent)

    deploy_result = {"exit_code": 99, "success": False}

    assert agent._fix_with_agent(
        deploy_result,
        None,
        attempt=1,
        max_attempts=2) is True
    prompt, cwd, timeout = stub_agent.calls[0]
    assert "prompt::context::1/2" in prompt
    assert cwd == str(agent.repo_path)
    assert timeout == 300


def test_fix_with_agent_handles_agent_errors(
        agent, stub_agent, monkeypatch):
    agent.agent = stub_agent
    stub_agent.raise_error = True

    def fake_prepare(self, *args, **kwargs):
        return "ctx"

    def fake_prompt(self, *args, **kwargs):
        return "prompt"

    agent._prepare_error_context = MethodType(fake_prepare, agent)
    agent._create_fix_prompt = MethodType(fake_prompt, agent)

    assert agent._fix_with_agent(
        {"exit_code": 1, "success": False}, None, attempt=1, max_attempts=2) is False


def test_prepare_error_context_truncates_long_outputs(agent):
    long_stdout = "a" * 3500
    long_stderr = "b" * 3500
    health_stdout = "c" * 4000
    health_stderr = "d" * 2500
    deploy_result = {
        "exit_code": 1,
        "success": False,
        "stdout": long_stdout,
        "stderr": long_stderr}
    health_result = {
        "exit_code": 1,
        "success": False,
        "stdout": health_stdout,
        "stderr": health_stderr}

    context = agent._prepare_error_context(deploy_result, health_result)

    assert "... (truncated, showing last 3000 chars)" in context
    assert long_stdout[-3000:] in context
    assert long_stderr[-3000:] in context
    assert health_stdout[-3000:] in context
    assert health_stderr[-2000:] in context


def test_create_fix_prompt_includes_repo_and_scripts(agent):
    context = "error context"
    prompt = agent._create_fix_prompt(context, attempt=2, max_attempts=5)

    assert str(agent.repo_path) in prompt
    assert str(agent.deploy_script) in prompt
    assert ".sds/health_check.sh" in prompt
    assert "error context" in prompt
    assert "2 of 5" in prompt
