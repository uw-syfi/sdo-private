from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

from libs.agent_cli.codex import (
    CodexSessionIdError,
    CodexStructuredExecutionError,
    CodexStructuredOutputError,
    run_codex_structured,
)
from sdo.contracts import IncidentRequest, IncidentResult

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ResponderExecutionError(RuntimeError):
    pass


def main() -> int:
    try:
        request = IncidentRequest.model_validate_json(sys.stdin.read())
    except ValueError as exc:
        print(f"invalid incident request: {exc}", file=sys.stderr)
        return 2

    try:
        result = execute_incident(request)
    except ResponderExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.model_dump_json(exclude_none=True))
    return 0


def execute_incident(
    request: IncidentRequest,
    *,
    model: str | None = None,
    runner: CommandRunner | None = None,
) -> IncidentResult:
    try:
        completed = run_codex_structured(
            _responder_prompt(request),
            output_schema=_incident_result_schema(),
            cwd=request.repository_worktree,
            model=model or os.getenv("SDO_RESPONDER_MODEL") or None,
            timeout_seconds=None,
            sandbox="danger-full-access",
            runner=runner,
        )
    except CodexStructuredExecutionError as exc:
        raise ResponderExecutionError(str(exc) or "Codex responder failed") from exc
    except CodexSessionIdError as exc:
        raise ResponderExecutionError("Codex did not report a resumable session id") from exc
    except CodexStructuredOutputError as exc:
        raise ResponderExecutionError(f"invalid Codex incident result: {exc}") from exc
    try:
        result = IncidentResult.model_validate_json(completed.output_json)
    except (OSError, ValueError) as exc:
        raise ResponderExecutionError(f"invalid Codex incident result: {exc}") from exc
    if result.responder_session_id is None:
        result = result.model_copy(update={"responder_session_id": completed.session_id})
    if result.incident_id != request.incident_id:
        raise ResponderExecutionError("Codex result incident_id does not match request")
    return result


def _incident_result_schema() -> dict[str, object]:
    object_ref = {
        "type": "object",
        "properties": {
            "api_version": {"type": "string"},
            "kind": {"type": "string", "minLength": 1},
            "namespace": {"type": "string"},
            "name": {"type": "string", "minLength": 1},
        },
        "required": ["api_version", "kind", "namespace", "name"],
        "additionalProperties": False,
    }
    properties: dict[str, object] = {
        "incident_id": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": ["completed", "failed", "cancelled"]},
        "confirmed_root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "minLength": 1},
                    "resources": {"type": "array", "items": object_ref, "minItems": 1},
                },
                "required": ["summary", "resources"],
                "additionalProperties": False,
            },
        },
        "applied_playbooks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        "repair_changes": {"type": "array", "items": {"type": "string"}},
        "verification_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "passed": {"type": "boolean"},
                    "details": {"type": "string", "minLength": 1},
                    "observed_at": {"type": "string", "format": "date-time"},
                },
                "required": ["name", "passed", "details", "observed_at"],
                "additionalProperties": False,
            },
        },
        "usage": {
            "type": "object",
            "properties": {
                "llm_calls": {"type": "integer", "minimum": 0},
                "input_tokens": {"type": "integer", "minimum": 0},
                "output_tokens": {"type": "integer", "minimum": 0},
            },
            "required": ["llm_calls", "input_tokens", "output_tokens"],
            "additionalProperties": False,
        },
        "timing": {
            "type": "object",
            "properties": {
                "started_at": {"type": "string", "format": "date-time"},
                "completed_at": {"type": "string", "format": "date-time"},
            },
            "required": ["started_at", "completed_at"],
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _responder_prompt(request: IncidentRequest) -> str:
    extra_instructions = os.getenv("SDO_RESPONDER_EXTRA_INSTRUCTIONS", "").strip()
    additional_context = (
        f"\nAdditional environment-specific instructions:\n{extra_instructions}\n" if extra_instructions else ""
    )
    return (
        f"You are the SDO incident responder for incident {request.incident_id}.\n\n"
        "Work autonomously in the supplied repository and Kubernetes namespace to resolve every triggering finding. "
        "Confirm a surfaced playbook against live state before applying it, repair the source of truth, redeploy when "
        "needed, and collect evidence that the independent health objective is restored. During this response, "
        "`.sdo/` is read-only. Do not create, edit, or delete any path under `.sdo/`. The controller independently "
        "verifies recovery after this response and records the authoritative outcome; only then may the broker open "
        "a same-session reflection turn with narrowly scoped write access. Return only the IncidentResult JSON "
        "required by the output schema.\n\n"
        f"{additional_context}\n"
        f"Incident request:\n{request.model_dump_json(indent=2)}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
