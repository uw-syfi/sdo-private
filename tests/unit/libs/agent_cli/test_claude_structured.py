from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from libs.agent_cli.claude_structured import resume_claude_structured, run_claude_structured

if TYPE_CHECKING:
    from pathlib import Path


def _runner(calls: list[tuple[list[str], dict[str, object]]]):
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        payload = {
            "type": "result",
            "session_id": "session-123",
            "is_error": False,
            "structured_output": {"ok": True},
            "num_turns": 3,
            "total_cost_usd": 0.012,
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "output_tokens": 40,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload) + "\n", "")

    return run


def test_run_claude_structured_uses_schema_stdin_and_returns_session(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    result = run_claude_structured(
        "do the work",
        output_schema={"type": "object"},
        cwd=tmp_path,
        model="haiku",
        effort="low",
        runner=_runner(calls),
    )

    command, kwargs = calls[0]
    assert command[:2] == ["claude", "-p"]
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--effort") + 1] == "low"
    assert json.loads(command[command.index("--json-schema") + 1]) == {"type": "object"}
    assert kwargs["input"] == "do the work"
    assert kwargs["cwd"] == tmp_path
    assert result.output_json == '{"ok": true}'
    assert result.session_id == "session-123"
    assert result.usage.provider == "claude"
    assert result.usage.tokens.input_tokens == 150
    assert result.usage.tokens.cached_input_tokens == 50
    assert result.usage.tokens.cache_write_input_tokens == 20
    assert result.usage.tokens.output_tokens == 40
    assert result.usage.tokens.turns == 3
    assert result.usage.total_cost_usd == 0.012


def test_resume_claude_structured_preserves_session(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    result = resume_claude_structured(
        "session-123",
        "reflect",
        output_schema={"type": "object"},
        cwd=tmp_path,
        runner=_runner(calls),
    )

    command, _kwargs = calls[0]
    assert command[command.index("--resume") + 1] == "session-123"
    assert result.session_id == "session-123"


def test_workspace_write_confines_reads_without_disabling_edit_tools(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    run_claude_structured(
        "edit and validate",
        output_schema={"type": "object"},
        cwd=tmp_path,
        sandbox="workspace-write",
        runner=_runner(calls),
    )

    command, _kwargs = calls[0]
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False
    assert "denyWrite" not in settings["sandbox"].get("filesystem", {})
    assert "--disallowedTools" not in command
    assert "hooks" in settings
