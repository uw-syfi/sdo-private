"""The structured-turn contract SDO's agents run through.

Every SDO role picks a provider and an access level; these tests pin what
each combination actually asks the CLI for, and how failures surface.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from agentshim.providers.codex import BYPASS_FLAG, RULES_FILENAME, CodexSandboxConfig, parse_rules, parse_sandbox
from agentshim.testing import FakeRun, scripted_turn

from libs.agent_cli.structured import (
    ACCESS_MODES,
    AGENT_PROVIDERS,
    DETECTOR_GATEWAY_COMMAND,
    TURN_USAGE_LOG_ENV,
    StructuredTurnError,
    StructuredTurnTimeout,
    run_structured_turn,
)
from tests.structured_turns import FakeCodexLogin, ScriptedAgent, failure, fake_codex_login, reply

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentshim import CommandRequest

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def codex_login(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCodexLogin]:
    with fake_codex_login(monkeypatch) as login:
        yield login


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


class TestCodexGatewayHome:
    """Codex reads the detector-gateway exemption only from ``$CODEX_HOME/rules``."""

    @staticmethod
    def _snapshot(request: CommandRequest) -> dict[str, Any]:
        """What the per-turn home holds while the CLI runs; it is gone afterwards."""
        home = Path(request.env["CODEX_HOME"])
        rules = home / "rules" / RULES_FILENAME
        auth = home / "auth.json"
        return {
            "home": home,
            "rules": parse_rules(rules.read_text(encoding="utf-8")) if rules.is_file() else None,
            "auth": auth.read_text(encoding="utf-8") if auth.is_file() else None,
            "auth_mode": auth.stat().st_mode & 0o777 if auth.is_file() else None,
            "entries": sorted(path.name for path in home.iterdir()),
        }

    def _run_capturing(self, tmp_path: Path, run: Any, **options: Any) -> tuple[list[dict[str, Any]], Any]:
        seen: list[dict[str, Any]] = []

        def respond(request: CommandRequest) -> Any:
            seen.append(self._snapshot(request))
            return run

        agent = ScriptedAgent(respond)
        try:
            turn = run_structured_turn(
                "codex",
                "prompt",
                output_schema=_SCHEMA,
                cwd=tmp_path,
                access="workspace-write",
                executor=agent.executor,
                **options,
            )
        except StructuredTurnError as exc:
            return seen, exc
        return seen, turn

    def test_workspace_write_exempts_the_gateway_through_a_private_home(
        self, tmp_path: Path, codex_login: FakeCodexLogin
    ) -> None:
        (seen,), turn = self._run_capturing(tmp_path, reply("codex", {"answer": "ok"}, session_id="s-1"))

        assert json.loads(turn.output_json) == {"answer": "ok"}
        assert seen["rules"] == [DETECTOR_GATEWAY_COMMAND.split()]
        assert seen["auth"] == codex_login.auth.read_text(encoding="utf-8")
        assert seen["auth_mode"] == 0o600
        assert seen["entries"] == ["auth.json", "rules"]
        home = seen["home"]
        assert home.is_absolute()
        assert home.parent == codex_login.homes_root
        assert home != codex_login.auth.parent

    @pytest.mark.parametrize("succeeds", [True, False])
    def test_a_login_refreshed_during_the_turn_is_kept(
        self, tmp_path: Path, codex_login: FakeCodexLogin, *, succeeds: bool
    ) -> None:
        """Codex rotates refresh tokens, so dropping the copy would log the user out."""

        def refresh_then_respond(request: CommandRequest) -> Any:
            (Path(request.env["CODEX_HOME"]) / "auth.json").write_text('{"tokens": "rotated"}', encoding="utf-8")
            return reply("codex", {"answer": "ok"}, session_id="s-1") if succeeds else failure(stderr="boom")

        agent = ScriptedAgent(refresh_then_respond)
        with contextlib.suppress(StructuredTurnError):
            run_structured_turn(
                "codex",
                "prompt",
                output_schema=_SCHEMA,
                cwd=tmp_path,
                access="workspace-write",
                executor=agent.executor,
            )

        assert codex_login.auth.read_text(encoding="utf-8") == '{"tokens": "rotated"}'
        assert codex_login.auth.stat().st_mode & 0o777 == 0o600

    def test_an_unchanged_login_is_not_rewritten(self, tmp_path: Path, codex_login: FakeCodexLogin) -> None:
        before = codex_login.auth.stat().st_mtime_ns
        self._run_capturing(tmp_path, reply("codex", {"answer": "ok"}, session_id="s-1"))
        assert codex_login.auth.stat().st_mtime_ns == before

    def test_the_private_home_is_outside_the_workspace_and_tmp(
        self, tmp_path: Path, codex_login: FakeCodexLogin
    ) -> None:
        (seen,), _ = self._run_capturing(tmp_path, reply("codex", {"answer": "ok"}, session_id="s-1"))

        home = os.path.realpath(seen["home"])
        for writable in (str(tmp_path), "/tmp"):
            parent = os.path.realpath(writable)
            assert os.path.commonpath([home, parent]) != parent

    @pytest.mark.parametrize(
        "run",
        [
            pytest.param(reply("codex", {"answer": "ok"}, session_id="s-1"), id="success"),
            pytest.param(failure(stderr="boom\n"), id="cli-failure"),
            pytest.param(reply("codex", {"answer": "ok"}, session_id=None), id="no-session"),
        ],
    )
    def test_the_private_home_is_removed_after_the_turn(
        self, tmp_path: Path, codex_login: FakeCodexLogin, run: Any
    ) -> None:
        (seen,), _ = self._run_capturing(tmp_path, run)

        assert not seen["home"].exists()
        assert list(codex_login.homes_root.iterdir()) == []

    def test_a_missing_login_is_reported_without_an_api_key(
        self, tmp_path: Path, codex_login: FakeCodexLogin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codex_login.auth.unlink()
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        seen, error = self._run_capturing(tmp_path, reply("codex", {"answer": "ok"}, session_id="s-1"))

        assert seen == []
        assert isinstance(error, StructuredTurnError)
        assert "auth.json" in str(error)
        assert "codex login" in str(error)

    @pytest.mark.parametrize("variable", ["CODEX_API_KEY", "OPENAI_API_KEY"])
    def test_an_api_key_replaces_the_login(
        self, tmp_path: Path, codex_login: FakeCodexLogin, monkeypatch: pytest.MonkeyPatch, variable: str
    ) -> None:
        codex_login.auth.unlink()
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv(variable, "sk-test")

        (seen,), turn = self._run_capturing(tmp_path, reply("codex", {"answer": "ok"}, session_id="s-1"))

        assert json.loads(turn.output_json) == {"answer": "ok"}
        assert seen["entries"] == ["rules"]
        assert seen["rules"] == [DETECTOR_GATEWAY_COMMAND.split()]

    @pytest.mark.parametrize("access", ["read-only", "danger-full-access"])
    def test_other_levels_keep_the_launching_codex_home(
        self, access: str, tmp_path: Path, codex_login: FakeCodexLogin
    ) -> None:
        _, agent = _run("codex", tmp_path, access=access)

        (request,) = agent.requests
        assert request.env["CODEX_HOME"] == str(codex_login.auth.parent)
        assert not codex_login.homes_root.exists()

    def test_a_workspace_write_turn_cannot_be_resumed(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot be resumed"):
            _run("codex", tmp_path, access="workspace-write", resume_session_id="earlier")


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


@pytest.mark.parametrize("provider", AGENT_PROVIDERS)
def test_each_turn_appends_its_usage_to_the_configured_log(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "usage" / "turns.jsonl"
    monkeypatch.setenv(TURN_USAGE_LOG_ENV, str(log))

    _run(provider, tmp_path, model="m-1")
    _run(provider, tmp_path, model="m-1")

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["provider"] == provider
    assert records[0]["model"] == "m-1"
    assert records[0]["session_id"] == "s-1"
    assert set(records[0]["usage"]) >= {"llm_calls", "input_tokens", "output_tokens", "cached_input_tokens"}
    assert records[0]["duration_seconds"] >= 0


def test_turns_write_no_usage_log_unless_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TURN_USAGE_LOG_ENV, raising=False)
    _run("codex", tmp_path)
    assert not list(tmp_path.rglob("*.jsonl"))
