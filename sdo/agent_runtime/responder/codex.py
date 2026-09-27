from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from libs.agent_cli.structured import AGENT_PROVIDERS, StructuredTurnError, run_structured_turn
from sdo.contracts import DetectorEvaluation, DetectorEvaluationStatus, Finding, IncidentRequest, IncidentResult
from sdo.operational_memory import MemoryRepository, MemoryRepositoryError, WarmPlaybookMatch, warm_playbook_matches

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


#: Upper bounds on playbook text inlined into a warm-path prompt.
_WARM_PLAYBOOK_MAX_CHARS = 8_000
_WARM_SCRIPTS_MAX_CHARS = 8_000


@dataclass(frozen=True)
class WarmPlaybook:
    """A validated incident playbook inlined for an exact-match incident."""

    path: str
    text: str
    scripts: tuple[tuple[str, str], ...]
    match: WarmPlaybookMatch

    def __post_init__(self) -> None:
        if self.path != self.match.path:
            raise ValueError("warm playbook path must equal its match path")

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.match.findings

    def precondition_script(self) -> str | None:
        """The playbook's diagnose (preferred) or verify script, for a not-yet-fired detector's sanity check."""

        for keyword in _PRECONDITION_SCRIPT_KEYWORDS:
            for script_path, _text in self.scripts:
                if keyword in PurePosixPath(script_path).stem.lower():
                    return script_path
        return None


#: Script-name keywords, in preference order, for the one sanity check of a not-yet-fired detector.
_PRECONDITION_SCRIPT_KEYWORDS = ("diagnose", "check", "verify")


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... truncated: {len(text) - limit} more characters omitted ...]\n"


def _contained_file(worktree: Path, relative: str, root: Path) -> Path | None:
    candidate = (worktree / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def warm_playbooks(request: IncidentRequest) -> list[WarmPlaybook]:
    """Warm incident-detector-owned playbooks for an exact-match incident, read from the incident worktree."""

    worktree = Path(request.repository_worktree)
    try:
        manifest = MemoryRepository(worktree).diagnostics()
    except (MemoryRepositoryError, OSError, ValueError):
        return []
    matches = warm_playbook_matches(request, manifest)
    playbook_root = (worktree / ".sdo" / "playbooks").resolve()
    playbooks: list[WarmPlaybook] = []
    seen: set[str] = set()
    for match in matches:
        if match.path in seen:
            continue
        readme = _contained_file(worktree, match.path, playbook_root)
        if readme is None:
            continue
        seen.add(match.path)
        scripts: list[tuple[str, str]] = []
        budget = _WARM_SCRIPTS_MAX_CHARS
        for script in sorted((readme.parent / "scripts").glob("*.sh")):
            if budget <= 0 or not script.is_file():
                break
            text = _bounded(script.read_text(encoding="utf-8", errors="replace"), budget)
            budget -= len(text)
            scripts.append((script.relative_to(worktree.resolve()).as_posix(), text))
        playbooks.append(
            WarmPlaybook(
                path=match.path,
                text=_bounded(readme.read_text(encoding="utf-8", errors="replace"), _WARM_PLAYBOOK_MAX_CHARS),
                scripts=tuple(scripts),
                match=match,
            )
        )
    return playbooks


#: goal.md is inlined only when it is at most this long; otherwise it stays a path.
_GOAL_INLINE_MAX_CHARS = 4_000


def _detector_evidence(request: IncidentRequest) -> str:
    """The latest evaluation per detector, plus its latest firing one when that is older."""

    latest: dict[str, DetectorEvaluation] = {}
    latest_firing: dict[str, DetectorEvaluation] = {}
    for evaluation in sorted(request.detector_history, key=lambda item: item.evaluated_at):
        latest[evaluation.detector_id] = evaluation
        if evaluation.status == DetectorEvaluationStatus.FIRING:
            latest_firing[evaluation.detector_id] = evaluation

    def line(evaluation: DetectorEvaluation) -> str:
        text = f"{evaluation.detector_id}: {evaluation.status.value} at {_timestamp(evaluation.evaluated_at)}"
        if evaluation.fingerprints:
            text += f" [{', '.join(evaluation.fingerprints)}]"
        if evaluation.error:
            text += f" error: {evaluation.error}"
        return f"- {text}\n"

    lines = []
    for detector_id in sorted(latest):
        firing = latest_firing.get(detector_id)
        if firing is not None and firing is not latest[detector_id]:
            lines.append(line(firing))
        lines.append(line(latest[detector_id]))
    return "Latest detector evidence (compacted from detector_history):\n" + "".join(lines)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _inlined_health_objective(request: IncidentRequest) -> str:
    worktree = Path(request.repository_worktree)
    goal = _contained_file(worktree, request.health_objective_path, worktree.resolve())
    if goal is None:
        return ""
    try:
        text = goal.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > _GOAL_INLINE_MAX_CHARS:
        return ""
    return f"Health objective (`{request.health_objective_path}`, inlined; do not re-read it):\n{text.rstrip()}\n\n"


def _cold_instructions() -> str:
    return (
        "The incident request may contain compact relevant_outcomes selected deterministically from prior verified "
        "successes. Treat them as hypotheses, compare them with live state, and adapt or reject them explicitly; "
        "never replay a prior action without confirming its assumptions. "
        "Confirm a surfaced playbook against live state before applying it, repair the source of truth, redeploy when "
        "needed, and collect evidence that the independent health objective is restored. "
    )


def _fired_evidence(playbook: WarmPlaybook) -> str:
    findings = ", ".join(
        sorted({f"{finding.detector_id} ({finding.fingerprint or finding.rule_id})" for finding in playbook.findings})
    )
    return (
        f"- `{playbook.path}`: the finding(s) {findings} come from its validated incident detector. That evidence "
        "already establishes the playbook's preconditions: do not re-diagnose them. The sanity check confirms the "
        "specific finding evidence is still live (for example the missing resource is still absent and the "
        "affected workload still fails); it replaces the playbook's own diagnosis steps.\n"
    )


def _pending_evidence(playbook: WarmPlaybook, namespace: str) -> str:
    detector = playbook.match.detector
    kinds = ", ".join(sorted({f"{watch.kind} ({watch.api_version})" for watch in detector.watches}))
    watched = f"the detector's watched resources ({kinds})" if kinds else "the resources the playbook names"
    script = playbook.precondition_script()
    script_step = (
        f" Run `{script}` inside that check with the incident's values; a verify script is expected to fail "
        "before the repair, and its failure must point at the playbook's fault."
        if script is not None
        else ""
    )
    return (
        f"- `{playbook.path}`: owned by the registered incident detector `{detector.id}`, which has not fired for "
        "this incident, so no incident-detector evidence exists yet. The sanity check must confirm the playbook's "
        f"preconditions itself: inspect {watched} in namespace `{namespace}` "
        "(plus the pods and recent events of the affected workload) against the preconditions the playbook "
        f"states.{script_step}\n"
    )


def _warm_instructions(playbooks: list[WarmPlaybook], namespace: str) -> str:
    evidence = "".join(
        _fired_evidence(playbook) if playbook.match.detector_fired else _pending_evidence(playbook, namespace)
        for playbook in playbooks
    )
    priors = sorted({incident for playbook in playbooks for incident in playbook.match.prior_incidents})
    sections = []
    for playbook in playbooks:
        section = f"--- Playbook `{playbook.path}` (inlined; do not re-read it) ---\n{playbook.text.rstrip()}\n"
        for script_path, script_text in playbook.scripts:
            section += f"--- Playbook script `{script_path}` ---\n{script_text.rstrip()}\n"
        sections.append(section)
    return (
        "Warm path: validated incident memory matches this incident. "
        f"The prior verified outcome(s) {', '.join(priors)} match this incident by exact fingerprint, and each "
        "playbook below is owned by a responder-owned incident detector that SDO's commit broker validated "
        "(matching and near-miss tests) and learned from a prior independently verified success. Do not re-read "
        "the playbook, and do not explore the repository or cluster beyond what the steps below need.\n"
        f"Playbook evidence:\n{evidence}"
        "Fast procedure:\n"
        "1. Run one combined sanity check: a single shell command covering the evidence above.\n"
        "2. If it agrees, submit the diagnosis through any configured environment-specific channel, then apply the "
        "playbook's repair exactly (use its scripts when present). After restoring a missing ConfigMap or Secret "
        "that a pod failed to mount, delete the stuck pods or rollout-restart their workload instead of waiting "
        "for the kubelet mount backoff.\n"
        "3. Run the playbook's verification once, then submit mitigation through any configured channel and return "
        "the IncidentResult, listing the applied playbook's path exactly as shown above in applied_playbooks.\n"
        "Fall back to a full investigation only if the sanity check contradicts the playbook's preconditions, the "
        "repair fails, or the verification fails; then treat relevant_outcomes as hypotheses and confirm "
        "assumptions against live state. Investigate any other active finding the playbook's fault does not "
        "explain normally.\n\n" + "\n".join(sections) + "\n"
    )


def _responder_prompt(request: IncidentRequest) -> str:
    extra_instructions = os.getenv("SDO_RESPONDER_EXTRA_INSTRUCTIONS", "").strip()
    additional_context = (
        f"\nAdditional environment-specific instructions:\n{extra_instructions}\n" if extra_instructions else ""
    )
    playbooks = warm_playbooks(request)
    strategy = _warm_instructions(playbooks, request.namespace) if playbooks else _cold_instructions()
    return (
        f"You are the SDO incident responder for incident {request.incident_id}.\n\n"
        "Work autonomously in the supplied repository and Kubernetes namespace to resolve every triggering finding. "
        f"{strategy}"
        "During this response, "
        "`.sdo/` is read-only. Do not create, edit, or delete any path under `.sdo/`. The controller independently "
        "verifies recovery after this response and records the authoritative outcome; only then may the broker open "
        "a same-session reflection turn with narrowly scoped write access. Return only the IncidentResult JSON "
        "required by the output schema.\n\n"
        f"Repair evidence mode: {request.repair_policy}. For every live mutation, return a repair action receipt "
        "with its target, timing, result, and reversibility. In recorded-actions mode, a successful live-only "
        "repair must have at least one successful receipt; repository changes are still committed when present.\n\n"
        f"{additional_context}\n"
        f"{_inlined_health_objective(request)}"
        f"{_detector_evidence(request)}\n"
        f"Incident request:\n{request.model_dump_json(indent=2, exclude={'detector_history'})}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
