from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

from libs.agent_cli.structured import AGENT_PROVIDERS, StructuredTurnError, run_structured_turn
from sdo.contracts import IncidentRequest, IncidentResult

if TYPE_CHECKING:
    from agentshim import CommandExecutor


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
    provider: str | None = None,
    executor: CommandExecutor | None = None,
) -> IncidentResult:
    selected_provider = (provider or os.getenv("SDO_AGENT_PROVIDER") or "codex").strip().lower()
    if selected_provider not in AGENT_PROVIDERS:
        raise ResponderExecutionError(f"unsupported agent provider {selected_provider!r}")
    try:
        completed = run_structured_turn(
            selected_provider,  # type: ignore[arg-type]
            _responder_prompt(request),
            output_schema=_incident_result_schema(),
            cwd=request.repository_worktree,
            access="danger-full-access",
            model=model or os.getenv("SDO_RESPONDER_MODEL") or None,
            reasoning_effort="medium",
            executor=executor,
        )
    except StructuredTurnError as exc:
        raise ResponderExecutionError(f"{selected_provider} responder failed: {exc}") from exc
    try:
        payload = json.loads(completed.output_json)
        tokens = completed.usage.tokens
        payload["usage"] = {
            "llm_calls": tokens.turns,
            "input_tokens": tokens.input_tokens,
            "output_tokens": tokens.output_tokens,
            "cached_input_tokens": tokens.cached_input_tokens,
            "cache_write_input_tokens": tokens.cache_write_input_tokens,
            "reasoning_output_tokens": tokens.reasoning_output_tokens,
            "total_cost_usd": completed.usage.total_cost_usd,
        }
        result = IncidentResult.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise ResponderExecutionError(f"invalid {selected_provider} incident result: {exc}") from exc
    if result.responder_session_id is None:
        result = result.model_copy(update={"responder_session_id": completed.session_id})
    if result.incident_id != request.incident_id:
        raise ResponderExecutionError(f"{selected_provider} result incident_id does not match request")
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
        "repair_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_id": {"type": "string", "minLength": 1},
                    "kind": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "details": {"type": "string", "minLength": 1},
                    "started_at": {"type": "string", "format": "date-time"},
                    "completed_at": {"type": "string", "format": "date-time"},
                    "success": {"type": "boolean"},
                    "reversible": {"type": "boolean"},
                },
                "required": [
                    "action_id",
                    "kind",
                    "target",
                    "summary",
                    "details",
                    "started_at",
                    "completed_at",
                    "success",
                    "reversible",
                ],
                "additionalProperties": False,
            },
        },
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
        "The incident request may contain compact relevant_outcomes selected deterministically from prior verified "
        "successes. Treat them as hypotheses, compare them with live state, and adapt or reject them explicitly; "
        "never replay a prior action without confirming its assumptions. "
        "Confirm a surfaced playbook against live state before applying it, repair the source of truth, redeploy when "
        "needed, and collect evidence that the independent health objective is restored. During this response, "
        "`.sdo/` is read-only. Do not create, edit, or delete any path under `.sdo/`. The controller independently "
        "verifies recovery after this response and records the authoritative outcome; only then may the broker open "
        "a same-session reflection turn with narrowly scoped write access. Return only the IncidentResult JSON "
        "required by the output schema.\n\n"
        f"Repair evidence mode: {request.repair_policy}. For every live mutation, return a repair action receipt "
        "with its target, timing, result, and reversibility. In recorded-actions mode, a successful live-only "
        "repair must have at least one successful receipt; repository changes are still committed when present.\n\n"
        f"{additional_context}\n"
        f"Incident request:\n{request.model_dump_json(indent=2)}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
