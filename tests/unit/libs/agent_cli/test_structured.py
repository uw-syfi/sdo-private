"""The structured-turn contract SDO's agents run through.

Every SDO role picks a provider and an access level; these tests pin what
each combination actually asks the CLI for, and how failures surface.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from agentshim.providers.codex import BYPASS_FLAG, CodexSandboxConfig, parse_sandbox
from agentshim.testing import FakeRun, scripted_turn

from libs.agent_cli.structured import (
    ACCESS_MODES,
    AGENT_PROVIDERS,
    DETECTOR_GATEWAY_COMMAND,
    StructuredTurnError,
    StructuredTurnTimeout,
    run_structured_turn,
)
from tests.structured_turns import ScriptedAgent, failure, reply

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _run(
    provider: str,
    tmp_path: Path,
    run: FakeRun | None = None,
    **options: Any,
) -> tuple[Any, ScriptedAgent]:
    agent = ScriptedAgent(lambda _request: run or reply(provider, {"answer": "ok"}, session_id="s-1"))
    turn = run_structured_turn(
        provider,  # type: ignore[arg-type]
        "prompt",
        output_schema=_SCHEMA,
        cwd=tmp_path,
        access=options.pop("access", "read-only"),
        executor=agent.executor,
        **options,
    )
    return turn, agent


def _settings(argv: list[str]) -> dict[str, Any] | None:
    if "--settings" not in argv:
        return None
    return json.loads(argv[argv.index("--settings") + 1])


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
@pytest.mark.parametrize("access", ACCESS_MODES)
def test_every_combination_returns_the_structured_output_and_session(
    provider: str, access: str, tmp_path: Path
) -> None:
    turn, agent = _run(provider, tmp_path, access=access)

    assert json.loads(turn.output_json) == {"answer": "ok"}
    assert turn.session_id == "s-1"
    (request,) = agent.requests
    assert request.cwd == str(tmp_path.resolve())
    assert request.stdin == "prompt"
    assert agent.schemas == [_SCHEMA]


class TestCodexAccess:
    @pytest.mark.parametrize("access", ACCESS_MODES)
    def test_each_level_is_codex_own_sandbox_mode(self, access: str, tmp_path: Path) -> None:
        _, agent = _run("codex", tmp_path, access=access)
        (argv,) = agent.argvs
        assert BYPASS_FLAG not in argv
        assert parse_sandbox(argv) == CodexSandboxConfig(mode=access)  # type: ignore[arg-type]


class TestClaudeAccess:
    def test_full_access_passes_no_settings(self, tmp_path: Path) -> None:
        _, agent = _run("claude", tmp_path, access="danger-full-access")
        (argv,) = agent.argvs
        assert _settings(argv) is None
        assert "--disallowedTools" not in argv

    def test_read_only_denies_writes_and_the_edit_tools(self, tmp_path: Path) -> None:
        _, agent = _run("claude", tmp_path, access="read-only")
        (request,) = agent.requests
        argv = list(request.argv)
        settings = _settings(argv)
        assert settings is not None
        root = str(tmp_path.resolve())
        assert settings["sandbox"]["filesystem"]["denyWrite"] == [root]
        assert argv[argv.index("--disallowedTools") + 1] == "Edit,Write,NotebookEdit"
        assert shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])[-1] == root
        assert request.env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"

    def test_workspace_write_routes_compilation_through_the_detector_gateway(self, tmp_path: Path) -> None:
        _, agent = _run("claude", tmp_path, access="workspace-write")
        (request,) = agent.requests
        argv = list(request.argv)
        settings = _settings(argv)
        assert settings is not None
        assert "filesystem" not in settings["sandbox"]
        assert settings["sandbox"]["excludedCommands"] == [DETECTOR_GATEWAY_COMMAND]
        assert "--disallowedTools" not in argv
        matchers = [entry.get("matcher") for entry in settings["hooks"]["PreToolUse"]]
        assert "Bash" in matchers
        assert request.env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"

    @pytest.mark.parametrize(
        ("command", "denied"),
        [
            ("go test ./...", True),
            ("cd .sdo/diagnostics && go build ./...", True),
            ("GOFLAGS=-mod=mod go vet ./...", True),
            (DETECTOR_GATEWAY_COMMAND, False),
            ("gofmt -l .", False),
            ("cat go.mod", False),
        ],
    )
    def test_the_rendered_bash_hook_denies_direct_go_only(self, tmp_path: Path, command: str, *, denied: bool) -> None:
        """Run the hook exactly as Claude Code would, through a shell."""
        _, agent = _run("claude", tmp_path, access="workspace-write")
        settings = _settings(agent.argvs[0])
        assert settings is not None
        (bash_hook,) = [entry for entry in settings["hooks"]["PreToolUse"] if entry.get("matcher") == "Bash"]
        completed = subprocess.run(
            ["/bin/sh", "-c", bash_hook["hooks"][0]["command"]],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True,
            text=True,
            check=True,
        )
        decision = (
            json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] if completed.stdout else None
        )
        assert (decision == "deny") is denied


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_a_resumed_turn_continues_the_named_session(provider: str, tmp_path: Path) -> None:
    turn, agent = _run(
        provider, tmp_path, resume_session_id="earlier", run=reply(provider, {"answer": "ok"}, session_id=None)
    )
    (argv,) = agent.argvs
    if provider == "codex":
        assert argv[1:5] == ["exec", "resume", "earlier", "-"]
    else:
        assert argv[argv.index("--resume") + 1] == "earlier"
    assert turn.session_id == "earlier"


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_every_shell_command_is_reported_in_order(provider: str, tmp_path: Path) -> None:
    commands = ["ls", "cat /etc/hostname", "git status"]
    turn, _ = _run(provider, tmp_path, run=reply(provider, {"answer": "ok"}, session_id="s", commands=commands))
    assert list(dict.fromkeys(turn.shell_commands)) == commands


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_a_failed_cli_reports_both_streams(provider: str, tmp_path: Path) -> None:
    with pytest.raises(StructuredTurnError) as raised:
        _run(provider, tmp_path, run=failure(stdout="event log detail\n", stderr="the real cause\n"))
    message = str(raised.value)
    assert "the real cause" in message
    assert "event log detail" in message
    assert message.index("the real cause") < message.index("event log detail")


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_a_timeout_is_its_own_error(provider: str, tmp_path: Path) -> None:
    with pytest.raises(StructuredTurnTimeout) as raised:
        _run(provider, tmp_path, run=FakeRun(timeout=True), timeout_seconds=12)
    assert raised.value.timeout_seconds == 12


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_a_turn_without_structured_output_is_rejected(provider: str, tmp_path: Path) -> None:
    with pytest.raises(StructuredTurnError, match="no structured output"):
        _run(provider, tmp_path, run=scripted_turn(provider, text="no json here", session_id="s"))


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_a_fresh_turn_without_a_session_id_is_rejected(provider: str, tmp_path: Path) -> None:
    with pytest.raises(StructuredTurnError, match="session id"):
        _run(provider, tmp_path, run=reply(provider, {"answer": "ok"}, session_id=None))


def test_the_launching_environment_reaches_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Responder images put credentials in the process env, not a login shell."""
    monkeypatch.setenv("SDO_TEST_CREDENTIAL", "present")
    _, agent = _run("codex", tmp_path)
    assert agent.requests[0].env["SDO_TEST_CREDENTIAL"] == "present"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"provider": "gemini"}, "unsupported agent provider"),
        ({"access": "admin"}, "unsupported access mode"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"reasoning_effort": " "}, "reasoning_effort"),
        ({"resume_session_id": ""}, "resume_session_id"),
    ],
)
def test_invalid_arguments_fail_before_any_process_starts(
    tmp_path: Path, options: dict[str, Any], message: str
) -> None:
    agent = ScriptedAgent(lambda _request: FakeRun())
    arguments: dict[str, Any] = {"provider": "codex", "access": "read-only", **options}
    with pytest.raises(ValueError, match=message):
        run_structured_turn(
            arguments.pop("provider"),
            "prompt",
            output_schema=_SCHEMA,
            cwd=tmp_path,
            executor=agent.executor,
            **arguments,
        )
    assert agent.requests == []
