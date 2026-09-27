from __future__ import annotations

from typing import TYPE_CHECKING

from agentshim.providers.codex import CodexSandboxConfig, parse_sandbox

from sdo.agent_runtime.responder.reflection import ClaudeSessionBackend, CodexSessionBackend
from tests.structured_turns import ScriptedAgent, reply, turn_schema

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import CommandRequest
    from agentshim.testing import FakeRun


def test_codex_reflection_resumes_structured_session_in_incident_worktree(tmp_path: Path) -> None:
    def respond(request: CommandRequest) -> FakeRun:
        assert "proposed_changes" in turn_schema(request)["properties"]
        return reply(
            "codex",
            {
                "summary": "captured signature",
                "learning_decision": "updated",
                "proposed_changes": [".sdo/playbooks/example.md"],
            },
            session_id="session-1",
        )

    agent = ScriptedAgent(respond)
    backend = CodexSessionBackend(
        model="gpt-test",
        reasoning_effort="high",
        timeout_seconds=456,
        executor=agent.executor,
    )
    result = backend.resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect on verified recovery",
        idempotency_key="reflection:incident:commit",
    )

    assert result.summary == "captured signature"
    (request,) = agent.requests
    argv = list(request.argv)
    assert argv[1:5] == ["exec", "resume", "session-1", "-"]
    assert parse_sandbox(argv) == CodexSandboxConfig(mode="danger-full-access")
    assert argv[argv.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="high"' in argv
    assert request.cwd == str(tmp_path.resolve())
    assert request.timeout == 456
    assert (request.stdin or "").startswith("Idempotency key: reflection:incident:commit")


def test_claude_reflection_resumes_structured_session(tmp_path: Path) -> None:
    agent = ScriptedAgent(
        lambda _request: reply(
            "claude",
            {
                "summary": "existing memory covers the incident",
                "learning_decision": "no_change",
                "no_change_reason": "the existing playbook already captures this verified signature",
                "proposed_changes": [],
            },
            session_id="session-1",
        )
    )

    result = ClaudeSessionBackend(model="haiku", executor=agent.executor).resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect",
        idempotency_key="reflection:incident:commit",
    )

    (command,) = agent.argvs
    assert command[command.index("--resume") + 1] == "session-1"
    assert command[command.index("--model") + 1] == "haiku"
    assert result.learning_decision == "no_change"
