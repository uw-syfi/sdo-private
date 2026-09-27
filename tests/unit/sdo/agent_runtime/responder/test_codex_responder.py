from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentshim.providers.codex import CodexSandboxConfig, parse_sandbox
from agentshim.testing import FakeRun

from sdo.agent_runtime.responder.codex import (
    ResponderExecutionError,
    _incident_result_schema,
    _responder_prompt,
    execute_incident,
)
from sdo.contracts import IncidentRequest, IncidentResult
from tests.structured_turns import ScriptedAgent, failure


def _fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[5]
    return (root / "tests" / "fixtures" / "sdo" / "contracts" / name).read_text(encoding="utf-8")


def test_codex_responder_captures_resumable_session_id() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_fixture("incident_result.json")).model_copy(
        update={"responder_session_id": None}
    )
    message = {"type": "agent_message", "id": "msg", "text": result.model_dump_json(exclude_none=True)}
    agent = ScriptedAgent(
        lambda _request: FakeRun(
            stdout=[
                '{"type":"thread.started","thread_id":"session-from-codex"}\n',
                json.dumps({"type": "item.completed", "item": message}) + "\n",
                '{"type":"turn.completed","usage":{"input_tokens":321,"cached_input_tokens":123,"output_tokens":45}}\n',
            ]
        )
    )

    completed = execute_incident(request, model="gpt-5.5", executor=agent.executor)

    assert completed.responder_session_id == "session-from-codex"
    assert completed.usage.llm_calls == 1
    assert completed.usage.input_tokens == 321
    assert completed.usage.cached_input_tokens == 123
    assert completed.usage.output_tokens == 45
    (argv,) = agent.argvs
    assert argv[argv.index("--model") + 1] == "gpt-5.5"
    assert parse_sandbox(argv) == CodexSandboxConfig(mode="danger-full-access")
    assert agent.requests[0].cwd == str(Path(request.repository_worktree).resolve())


def test_claude_responder_captures_resumable_session_id() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_fixture("incident_result.json")).model_copy(
        update={"responder_session_id": None}
    )
    event = {
        "type": "result",
        "session_id": "session-from-claude",
        "is_error": False,
        "structured_output": result.model_dump(mode="json", exclude_none=True),
        "num_turns": 4,
        "total_cost_usd": 0.25,
        "usage": {
            "input_tokens": 500,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 200,
            "output_tokens": 75,
        },
    }
    init = {"type": "system", "subtype": "init", "session_id": "session-from-claude"}
    agent = ScriptedAgent(lambda _request: FakeRun(stdout=[json.dumps(init) + "\n", json.dumps(event) + "\n"]))

    completed = execute_incident(request, provider="claude", model="haiku", executor=agent.executor)

    assert completed.responder_session_id == "session-from-claude"
    assert completed.usage.llm_calls == 4
    assert completed.usage.input_tokens == 800
    assert completed.usage.cached_input_tokens == 300
    assert completed.usage.cache_write_input_tokens == 100
    assert completed.usage.output_tokens == 75
    assert completed.usage.total_cost_usd == 0.25
    (argv,) = agent.argvs
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--settings" not in argv


def test_responder_cannot_reflect_before_controller_verification(monkeypatch) -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))

    prompt = _responder_prompt(request)

    assert "`.sdo/` is read-only" in prompt
    assert "Do not create, edit, or delete any path under `.sdo/`" in prompt
    assert "independently verifies" in prompt
    assert "same-session reflection turn" in prompt
    assert "After recovery, reflect into `.sdo`" not in prompt

    assert "SREGym" not in prompt
    assert "benchmarks.sregym" not in prompt

    monkeypatch.setenv(
        "SDO_RESPONDER_EXTRA_INSTRUCTIONS",
        "Use the configured external incident transport, but never treat its response as health evidence.",
    )
    extended_prompt = _responder_prompt(request)
    assert "Use the configured external incident transport" in extended_prompt
    assert "never treat its response as health evidence" in extended_prompt


def test_codex_failure_reports_structured_events_and_stderr() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))

    agent = ScriptedAgent(
        lambda _request: failure(stdout='{"type":"error","message":"schema rejected"}\n', stderr="PATH warning\n")
    )

    with pytest.raises(ResponderExecutionError) as captured:
        execute_incident(request, executor=agent.executor)

    assert "schema rejected" in str(captured.value)
    assert "PATH warning" in str(captured.value)


def test_codex_output_schema_is_strict_and_requires_defaulted_fields() -> None:
    schema = _incident_result_schema()

    assert schema["required"] == list(schema["properties"])
    assert schema["additionalProperties"] is False
    applied = schema["properties"]["applied_playbooks"]["items"]
    assert applied["required"] == ["path"]
    assert "parameter_bindings" not in applied["properties"]
    assert "responder_session_id" not in schema["properties"]
    assert "usage" not in schema["properties"]
    action = schema["properties"]["repair_actions"]["items"]
    assert action["required"] == list(action["properties"])


def test_recorded_actions_policy_is_explicit_in_responder_prompt() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json")).model_copy(
        update={"repair_policy": "recorded-actions"}
    )

    prompt = _responder_prompt(request)

    assert "recorded-actions" in prompt
    assert "repair action receipt" in prompt
