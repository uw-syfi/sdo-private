from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app_operator.protocol.codex_responder import (
    ResponderExecutionError,
    _incident_result_schema,
    _responder_prompt,
    execute_incident,
)
from app_operator.protocol.models import IncidentRequest, IncidentResult


def _fixture(name: str) -> str:
    root = Path(__file__).resolve().parents[4]
    return (root / "tests" / "fixtures" / "sdo" / "protocol" / name).read_text(encoding="utf-8")


def test_codex_responder_captures_resumable_session_id() -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))
    result = IncidentResult.model_validate_json(_fixture("incident_result.json")).model_copy(
        update={"responder_session_id": None}
    )

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(result.model_dump_json(exclude_none=True), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"thread.started","thread_id":"session-from-codex"}\n',
            stderr="",
        )

    completed = execute_incident(request, runner=runner)

    assert completed.responder_session_id == "session-from-codex"


def test_responder_cannot_reflect_before_controller_verification(monkeypatch) -> None:
    request = IncidentRequest.model_validate_json(_fixture("incident_request.json"))

    prompt = _responder_prompt(request)

    assert "`.sdo/` is read-only" in prompt
    assert "Do not create, edit, or delete any path under `.sdo/`" in prompt
    assert "independently verifies" in prompt
    assert "same-session reflection turn" in prompt
    assert "After recovery, reflect into `.sdo`" not in prompt

    monkeypatch.setenv("SDO_SREGYM_SUBMISSION_BRIDGE", "1")
    bridge_prompt = _responder_prompt(request)
    assert "app_operator.sdo_sregym.submission diagnosis" in bridge_prompt
    assert "app_operator.sdo_sregym.submission mitigation" in bridge_prompt
    assert "submission transport only" in bridge_prompt
    assert "must not be used as health evidence" in bridge_prompt
    assert "may take several minutes" in bridge_prompt
    assert "poll its session_id" in bridge_prompt
    assert "Do not begin repair until the diagnosis response is acknowledged" in bridge_prompt
    assert "Do not return the IncidentResult until mitigation reports done" in bridge_prompt
    assert "immediately before mitigation submission" in bridge_prompt
    assert "ready endpoint address targeting the current rollout" in bridge_prompt
    assert "failed or merely completed helper Job is not repair evidence" in bridge_prompt


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
