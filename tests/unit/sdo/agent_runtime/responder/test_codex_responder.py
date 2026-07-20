from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from sdo.agent_runtime.responder.codex import (
    ResponderExecutionError,
    _incident_result_schema,
    _responder_prompt,
    execute_incident,
)
from sdo.contracts import IncidentRequest, IncidentResult


def _fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[5]
    return (root / "tests" / "fixtures" / "sdo" / "contracts" / name).read_text(encoding="utf-8")


def test_codex_responder_captures_resumable_session_id() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_fixture("incident_result.json")).model_copy(
        update={"responder_session_id": None}
    )
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(result.model_dump_json(exclude_none=True), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"thread.started","thread_id":"session-from-codex"}\n',
            stderr="",
        )

    completed = execute_incident(request, model="gpt-5.5", runner=runner)

    assert completed.responder_session_id == "session-from-codex"
    assert "--model" in commands[0]
    assert commands[0][commands[0].index("--model") + 1] == "gpt-5.5"


def test_codex_responder_uses_managed_process_group_execution_by_default() -> None:
    assert inspect.signature(execute_incident).parameters["runner"].default is None


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

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout='{"type":"error","message":"schema rejected"}\n',
            stderr="PATH warning\n",
        )

    with pytest.raises(ResponderExecutionError) as captured:
        execute_incident(request, runner=runner)

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
