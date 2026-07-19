from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from app_operator.protocol.models import IncidentRequest, IncidentResult

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


def execute_incident(request: IncidentRequest, *, runner: CommandRunner = subprocess.run) -> IncidentResult:
    with tempfile.TemporaryDirectory(prefix="sdo-codex-responder-") as temp_dir:
        temp_root = Path(temp_dir)
        schema_path = temp_root / "incident-result.schema.json"
        result_path = temp_root / "incident-result.json"
        schema_path.write_text(json.dumps(_incident_result_schema()), encoding="utf-8")
        completed = runner(
            [
                "codex",
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--cd",
                request.repository_worktree,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(result_path),
                "--json",
                "-",
            ],
            input=_responder_prompt(request),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            details = "\n".join(part.strip() for part in (completed.stderr, completed.stdout) if part.strip())
            raise ResponderExecutionError(details or "Codex responder failed")
        try:
            result = IncidentResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResponderExecutionError(f"invalid Codex incident result: {exc}") from exc
        if result.responder_session_id is None:
            session_id = _codex_session_id(completed.stdout)
            if session_id is None:
                raise ResponderExecutionError("Codex did not report a resumable session id")
            result = result.model_copy(update={"responder_session_id": session_id})
    if result.incident_id != request.incident_id:
        raise ResponderExecutionError("Codex result incident_id does not match request")
    return result


def _codex_session_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


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
    submission = ""
    if os.getenv("SDO_SREGYM_SUBMISSION_BRIDGE", "").strip() == "1":
        submission = (
            "\nSREGym submission transport is available. After evidence-based diagnosis, run "
            "`python3 -m app_operator.sdo_sregym.submission diagnosis '<diagnosis>'`; after repair and your own "
            "verification, run `python3 -m app_operator.sdo_sregym.submission mitigation '<mitigation>'`. The latter "
            "also sends the autonomous done signal and waits for deferred diagnosis grading. This is submission "
            "transport only: its response is benchmark grading and must not be used as health evidence. The "
            "controller alone determines closure from live "
            "detectors. Each submission may take several minutes. When invoking it through exec_command, use a "
            "30-second yield, retain any returned session_id, and poll its session_id with write_stdin until the "
            "process exits; a yielded or empty response is not completion. Do not begin repair until the diagnosis "
            "response is acknowledged. Before mitigation, do not treat an accepted API mutation, a restarted Pod, "
            "or a completed verification command as recovery. Wait for every affected workload rollout to finish; "
            "require desired, updated, ready, and available replica counts to agree; require each affected Service "
            "to expose at least one ready endpoint address targeting the current rollout; and exercise a "
            "representative request. A failed or merely completed helper Job is not repair evidence. Repeat these "
            "live checks immediately before mitigation submission, and do not submit while a rollout or endpoint "
            "update is still converging. Do not return the IncidentResult until mitigation reports done.\n"
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
        f"{submission}\n"
        f"Incident request:\n{request.model_dump_json(indent=2)}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
