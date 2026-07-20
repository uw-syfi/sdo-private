from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

import pytest

from libs.agent_cli import HttpMcpServer, StdioMcpServer
from libs.agent_cli import codex as codex_module
from libs.agent_cli.codex import CodexStructuredExecutionError, resume_codex_structured, run_codex_structured


def test_fresh_structured_execution_preserves_schema_session_and_options(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, kwargs=kwargs)
        schema_path = Path(command[command.index("--output-schema") + 1])
        output_path = Path(command[command.index("--output-last-message") + 1])
        assert json.loads(schema_path.read_text(encoding="utf-8")) == {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }
        output_path.write_text('{"answer":"done"}', encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='not json\n{"type":"thread.started","thread_id":"fresh-thread"}\n',
            stderr="",
        )

    result = run_codex_structured(
        "solve it",
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        cwd=tmp_path,
        executable="codex-custom",
        model="gpt-test",
        reasoning_effort="high",
        timeout_seconds=123,
        sandbox="read-only",
        mcp_servers=[
            HttpMcpServer(name="docs", url="https://mcp.invalid"),
            StdioMcpServer(name="local", command="python", args=["-m", "server"], env={"TOKEN": "test"}),
        ],
        runner=runner,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:4] == ["codex-custom", "exec", "--sandbox", "read-only"]
    assert command[command.index("--cd") + 1] == str(tmp_path.resolve())
    assert command[command.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="high"' in command
    assert 'mcp_servers.docs.url="https://mcp.invalid"' in command
    assert 'mcp_servers.local.command="python"' in command
    assert 'mcp_servers.local.args=["-m", "server"]' in command
    assert 'mcp_servers.local.env.TOKEN="test"' in command
    assert command[-1] == "-"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["input"] == "solve it"
    assert kwargs["timeout"] == 123
    assert result.output_json == '{"answer":"done"}'
    assert result.session_id == "fresh-thread"
    assert result.stdout.startswith("not json")


def test_structured_resume_uses_existing_session_cwd_and_danger_full_access(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, kwargs=kwargs)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"summary":"learned"}', encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"thread.started","thread_id":"existing-thread"}\n',
            stderr="",
        )

    result = resume_codex_structured(
        "existing-thread",
        "reflect",
        output_schema={"type": "object"},
        cwd=tmp_path,
        model="gpt-test",
        reasoning_effort="medium",
        timeout_seconds=321,
        sandbox="danger-full-access",
        runner=runner,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == ["codex", "exec", "resume"]
    assert 'sandbox_mode="danger-full-access"' in command
    assert "--json" in command
    assert command[-2:] == ["existing-thread", "-"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["timeout"] == 321
    assert result.output_json == '{"summary":"learned"}'
    assert result.session_id == "existing-thread"


def test_structured_execution_reports_stderr_and_json_events(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            7,
            stdout='{"type":"error","message":"schema rejected"}\n',
            stderr="PATH warning\n",
        )

    with pytest.raises(CodexStructuredExecutionError) as captured:
        run_codex_structured(
            "solve it",
            output_schema={"type": "object"},
            cwd=tmp_path,
            runner=runner,
        )

    assert captured.value.returncode == 7
    assert "PATH warning" in str(captured.value)
    assert "schema rejected" in str(captured.value)


def test_structured_execution_kills_process_group_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class TimedOutProcess:
        pid = 1234

        def __init__(self) -> None:
            self.communicate_calls = 0

        def communicate(self, **kwargs: object) -> tuple[str, str]:
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                timeout = kwargs.get("timeout")
                assert timeout is None or isinstance(timeout, (int, float))
                raise subprocess.TimeoutExpired(["codex"], timeout)
            return ("partial stdout", "partial stderr")

    process = TimedOutProcess()
    popen_kwargs: dict[str, object] = {}
    killed: list[tuple[int, signal.Signals]] = []

    def fake_popen(command: list[str], **kwargs: object) -> TimedOutProcess:
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(codex_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(codex_module.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(subprocess.TimeoutExpired) as captured:
        codex_module._run(["codex"], prompt="solve it", timeout_seconds=1, runner=None)

    assert popen_kwargs["start_new_session"] is True
    assert killed == [(1234, signal.SIGKILL)]
    assert captured.value.stdout == "partial stdout"
    assert captured.value.stderr == "partial stderr"
