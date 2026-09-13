from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sdo.agent_runtime.responder.reflection import ClaudeSessionBackend, CodexSessionBackend


def test_codex_reflection_uses_managed_process_group_execution_by_default() -> None:
    assert CodexSessionBackend().command_runner is None


def test_codex_reflection_resumes_structured_session_in_incident_worktree(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, kwargs=kwargs)
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert "proposed_changes" in json.loads(schema_path.read_text(encoding="utf-8"))["properties"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps({"summary": "captured signature", "proposed_changes": [".sdo/playbooks/example.md"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, '{"type":"thread.started","thread_id":"session-1"}\n', "")

    backend = CodexSessionBackend(
        executable="codex-custom",
        model="gpt-test",
        reasoning_effort="high",
        timeout_seconds=456,
        command_runner=runner,
    )
    result = backend.resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect on verified recovery",
        idempotency_key="reflection:incident:commit",
    )

    assert result.summary == "captured signature"
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == ["codex-custom", "exec", "resume"]
    assert 'sandbox_mode="danger-full-access"' in command
    assert command[-2:] == ["session-1", "-"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["timeout"] == 456
    assert str(kwargs["input"]).startswith("Idempotency key: reflection:incident:commit")


def test_claude_reflection_resumes_structured_session(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, kwargs=kwargs)
        event = {
            "type": "result",
            "session_id": "session-1",
            "is_error": False,
            "structured_output": {"summary": "learned", "proposed_changes": []},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(event) + "\n", "")

    result = ClaudeSessionBackend(model="haiku", command_runner=runner).resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect",
        idempotency_key="reflection:incident:commit",
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[command.index("--resume") + 1] == "session-1"
    assert command[command.index("--model") + 1] == "haiku"
    assert result.summary == "learned"
