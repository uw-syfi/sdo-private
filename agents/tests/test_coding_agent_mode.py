from types import MethodType, SimpleNamespace

import pytest

from app_operator import coding_agent_mode as cam
from app_operator.coding_agent_mode import CodingAgentOperator


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
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    return repo


@pytest.fixture
def stub_agent():
    return StubAgent()


@pytest.fixture
def operator(repo_path, stub_agent):
    return CodingAgentOperator(str(repo_path), health_check_interval=1, agent=stub_agent)


def test_ensure_scripts_exist_skips_generation(operator, stub_agent, monkeypatch):
    calls = {"count": 0}

    def fake_generate_scripts(*args, **kwargs):
        calls["count"] += 1
        return True, "ok"

    monkeypatch.setattr(cam, "generate_scripts", fake_generate_scripts)

    assert operator._ensure_scripts_exist() is True
    assert calls["count"] == 0  # scripts already present


def test_ensure_scripts_exist_generates_when_missing(tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    operator = CodingAgentOperator(str(repo), agent=stub_agent)

    captured = {}

    def fake_generate_scripts(directory, agent):
        captured["args"] = (directory, agent)
        sds_dir = repo / ".sds"
        sds_dir.mkdir(exist_ok=True)
        (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
        (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
        return True, "done"

    monkeypatch.setattr(cam, "generate_scripts", fake_generate_scripts)

    assert operator._ensure_scripts_exist() is True
    assert captured["args"] == (str(repo), stub_agent)


def test_ensure_scripts_exist_bubbles_generation_failure(tmp_path, stub_agent, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    operator = CodingAgentOperator(str(repo), agent=stub_agent)

    def fake_generate_scripts(directory, agent):
        return False, "boom"

    monkeypatch.setattr(cam, "generate_scripts", fake_generate_scripts)

    assert operator._ensure_scripts_exist() is False


def _bind_method(obj, name, func):
    setattr(obj, name, MethodType(func, obj))


def test_deploy_with_fixing_succeeds_without_fix(operator):
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

    def fake_run_deploy(self):
        return next(deploy_results)

    def fake_run_health(self):
        return next(health_results)

    def unexpected_fix(self, *args, **kwargs):
        raise AssertionError("fix should not be invoked on success")

    _bind_method(operator, "_run_deploy_script", fake_run_deploy)
    _bind_method(operator, "_run_health_check", fake_run_health)
    _bind_method(operator, "_fix_with_agent", unexpected_fix)

    assert operator._deploy_with_fixing(max_attempts=1) is True


def test_deploy_with_fixing_retries_after_failure(operator):
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

    def fake_run_deploy(self):
        return next(deploy_results)

    def fake_run_health(self):
        return next(health_results)

    def fake_fix(self, deploy_result, health_result, attempt, max_attempts):
        fix_calls.append((attempt, max_attempts, deploy_result["exit_code"]))
        return True

    _bind_method(operator, "_run_deploy_script", fake_run_deploy)
    _bind_method(operator, "_run_health_check", fake_run_health)
    _bind_method(operator, "_fix_with_agent", fake_fix)

    assert operator._deploy_with_fixing(max_attempts=3) is True
    assert fix_calls == [(1, 3, 1)]


def test_deploy_with_fixing_respects_max_attempts(operator):
    deploy_results = iter(
        [
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "boom"},
        ]
    )
    fix_calls = {"count": 0}

    def fake_run_deploy(self):
        return next(deploy_results)

    def fake_fix(self, *args, **kwargs):
        fix_calls["count"] += 1
        return False

    _bind_method(operator, "_run_deploy_script", fake_run_deploy)
    _bind_method(operator, "_fix_with_agent", fake_fix)

    assert operator._deploy_with_fixing(max_attempts=1) is False
    assert fix_calls["count"] == 1


def test_run_deploy_script_handles_subprocess_results(operator, monkeypatch):
    def fake_run(cmd, cwd, capture_output, text, timeout):
        assert cmd[0] == str(operator.deploy_script)
        assert cmd[1] == "start"
        assert cwd == str(operator.repo_path)
        return SimpleNamespace(returncode=0, stdout="all good", stderr="")

    monkeypatch.setattr(cam.subprocess, "run", fake_run)

    result = operator._run_deploy_script()
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["stdout"] == "all good"


def test_run_deploy_script_handles_timeouts(operator, monkeypatch):
    def fake_run(*_, **__):
        raise cam.subprocess.TimeoutExpired(cmd=["./deploy.sh", "start"], timeout=300)

    monkeypatch.setattr(cam.subprocess, "run", fake_run)

    result = operator._run_deploy_script()
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"]


def test_run_health_check_maps_failures(operator, monkeypatch):
    def fake_run(*_, **__):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad")

    monkeypatch.setattr(cam.subprocess, "run", fake_run)

    result = operator._run_health_check()
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert result["stderr"] == "bad"


def test_run_health_check_handles_runtime_errors(operator, monkeypatch):
    def fake_run(*_, **__):
        raise RuntimeError("boom")

    monkeypatch.setattr(cam.subprocess, "run", fake_run)

    result = operator._run_health_check()
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "Failed to run health check" in result["stderr"]


def test_fix_with_agent_skips_when_attempt_exceeds_max(operator, stub_agent):
    assert operator._fix_with_agent({"exit_code": 1, "success": False}, None, attempt=3, max_attempts=3) is False
    assert stub_agent.calls == []


def test_fix_with_agent_calls_agent_and_returns_success(operator, stub_agent, monkeypatch):
    operator.agent = stub_agent

    def fake_prepare(self, deploy_result, health_result):
        return "context"

    def fake_prompt(self, context, attempt, max_attempts):
        return f"prompt::{context}::{attempt}/{max_attempts}"

    operator._prepare_error_context = MethodType(fake_prepare, operator)
    operator._create_fix_prompt = MethodType(fake_prompt, operator)

    deploy_result = {"exit_code": 99, "success": False}

    assert operator._fix_with_agent(deploy_result, None, attempt=1, max_attempts=2) is True
    prompt, cwd, timeout = stub_agent.calls[0]
    assert "prompt::context::1/2" in prompt
    assert cwd == str(operator.repo_path)
    assert timeout == 300


def test_fix_with_agent_handles_agent_errors(operator, stub_agent, monkeypatch):
    operator.agent = stub_agent
    stub_agent.raise_error = True

    def fake_prepare(self, *args, **kwargs):
        return "ctx"

    def fake_prompt(self, *args, **kwargs):
        return "prompt"

    operator._prepare_error_context = MethodType(fake_prepare, operator)
    operator._create_fix_prompt = MethodType(fake_prompt, operator)

    assert operator._fix_with_agent({"exit_code": 1, "success": False}, None, attempt=1, max_attempts=2) is False


def test_prepare_error_context_truncates_long_outputs(operator):
    long_stdout = "a" * 3500
    long_stderr = "b" * 3500
    health_stdout = "c" * 4000
    health_stderr = "d" * 2500
    deploy_result = {"exit_code": 1, "success": False, "stdout": long_stdout, "stderr": long_stderr}
    health_result = {"exit_code": 1, "success": False, "stdout": health_stdout, "stderr": health_stderr}

    context = operator._prepare_error_context(deploy_result, health_result)

    assert "... (truncated, showing last 3000 chars)" in context
    assert long_stdout[-3000:] in context
    assert long_stderr[-3000:] in context
    assert health_stdout[-3000:] in context
    assert health_stderr[-2000:] in context


def test_create_fix_prompt_includes_repo_and_scripts(operator):
    context = "error context"
    prompt = operator._create_fix_prompt(context, attempt=2, max_attempts=5)

    assert str(operator.repo_path) in prompt
    assert str(operator.deploy_script) in prompt
    assert ".sds/health_check.sh" in prompt
    assert "error context" in prompt
    assert "2 of 5" in prompt
