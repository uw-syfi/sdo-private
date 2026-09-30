"""Compact incident brief for a fresh-session first reflection attempt.

A resumed reflection re-sends the responder's whole transcript on every model
request. The fresh mode instead hands a new session this brief: the closure's
live evidence (verbatim where it matters), the responder's structured result,
its shell commands when the per-turn usage log recorded them, and short
excerpts of the operational memory the incident touched. Every section has a
character budget so the brief stays under about 8K tokens.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from sdo.operational_memory import MemoryRepository, MemoryRepositoryError

if TYPE_CHECKING:
    from pathlib import Path

    from sdo.contracts import DetectorEvaluation, Finding, IncidentResult, ObjectRef
    from sdo.operational_memory import BrokerClosure

#: Upper bound on the whole brief: about 4 characters per token, under 8K tokens.
BRIEF_MAX_CHARS = 30_000
_FINDINGS_BUDGET = 12_000
_RESULT_BUDGET = 6_000
_COMMANDS_BUDGET = 6_000
_MEMORY_BUDGET = 9_000
_MAX_FULL_FINDINGS = 4
_EVIDENCE_MAX_CHARS = 3_000
_METADATA_MAX_CHARS = 1_200
_DETAILS_MAX_CHARS = 700
_COMMAND_MAX_CHARS = 300
_HEAD_COMMANDS = 10
_PLAYBOOK_EXCERPT_CHARS = 700
_MAX_PLAYBOOK_EXCERPTS = 4
_INDEX_EXCERPT_CHARS = 1_000
_DETECTOR_DESCRIPTION_CHARS = 500
_DETECTOR_PREDICATE_CHARS = 1_400
_DESCRIPTION_RE = re.compile(r'Description:\s*((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+|`[^`]*`)')
_DETECT_FUNC_RE = re.compile(r"func\s*\([^)]*\)\s*Detect\s*\(")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n[... {omitted} characters omitted ...]"


def _ref(ref: ObjectRef) -> str:
    location = f"{ref.namespace}/{ref.name}" if ref.namespace else ref.name
    version = f" ({ref.api_version})" if ref.api_version else ""
    return f"{ref.kind} {location}{version}"


def _fenced(text: str) -> str:
    fence = "~~~~" if "```" in text else "```"
    return f"{fence}\n{text}\n{fence}"


def _finding(finding: Finding) -> str:
    lines = [
        f"- [{finding.severity.value}] {finding.detector_id} / {finding.rule_id} ({finding.status.value}): "
        f"{finding.summary}",
        f"  primary: {_ref(finding.primary_resource)}",
    ]
    if finding.related_resources:
        lines.append("  related: " + "; ".join(_ref(ref) for ref in finding.related_resources[:10]))
    if finding.parameter_bindings:
        bindings = "; ".join(f"{role}={_ref(ref)}" for role, ref in sorted(finding.parameter_bindings.items()))
        lines.append(f"  parameter bindings: {bindings}")
    if finding.fingerprint:
        lines.append(f"  fingerprint: {finding.fingerprint}")
    if finding.playbooks:
        lines.append("  playbooks: " + ", ".join(finding.playbooks))
    lines.append("  evidence (verbatim):")
    lines.append(_fenced(_clip(finding.evidence, _EVIDENCE_MAX_CHARS)))
    if finding.metadata:
        metadata = json.dumps(finding.metadata, sort_keys=True, default=str)
        lines.append("  metadata (object shapes, verbatim JSON):")
        lines.append(_fenced(_clip(metadata, _METADATA_MAX_CHARS)))
    return "\n".join(lines)


def _history(evaluations: list[DetectorEvaluation]) -> list[str]:
    by_detector: dict[str, list[DetectorEvaluation]] = {}
    for evaluation in evaluations:
        by_detector.setdefault(evaluation.detector_id, []).append(evaluation)
    lines = []
    for detector_id, entries in sorted(by_detector.items()):
        statuses = ", ".join(entry.status.value for entry in entries[-6:])
        lines.append(
            f"- {detector_id}: {len(entries)} evaluation(s) {entries[0].evaluated_at.isoformat()} .. "
            f"{entries[-1].evaluated_at.isoformat()}; latest statuses: {statuses}"
        )
    return lines


def _states(label: str, evaluations: list[DetectorEvaluation]) -> list[str]:
    if not evaluations:
        return [f"{label}: none recorded"]
    rendered = []
    for state in evaluations:
        detail = f" fingerprints={state.fingerprints}" if state.fingerprints else ""
        error = f" error={state.error}" if state.error else ""
        rendered.append(f"{state.detector_id}={state.status.value}{detail}{error}")
    return [f"{label}: " + "; ".join(rendered)]


def _findings_section(closure: BrokerClosure) -> str:
    request = closure.request
    lines = [
        "## Live evidence at dispatch (closure request)",
        f"Application {request.application} in namespace {request.namespace}; source commit "
        f"{request.source_commit}, deployed commit {request.deployed_commit}.",
        f"Detected {closure.detected_at.isoformat()}, dispatched {closure.dispatched_at.isoformat()}, responder "
        f"completed {closure.responder_completed_at.isoformat()}, health verified {closure.verified_at.isoformat()}.",
        "Findings:",
    ]
    lines.extend(_finding(finding) for finding in request.findings[:_MAX_FULL_FINDINGS])
    lines.extend(
        f"- [{finding.severity.value}] {finding.detector_id} / {finding.rule_id}: {finding.summary} "
        f"({_ref(finding.primary_resource)}; evidence omitted)"
        for finding in request.findings[_MAX_FULL_FINDINGS:]
    )
    lines.append("Detector history before dispatch:")
    lines.extend(_history(request.detector_history))
    if request.surfaced_playbooks:
        lines.append("Surfaced playbooks: " + ", ".join(playbook.path for playbook in request.surfaced_playbooks))
    lines.extend(
        f"Prior outcome {prior.incident_id} ({prior.match_reason}): root causes {prior.root_cause_summaries}; "
        f"repairs {prior.repair_action_summaries}; applied {prior.applied_playbooks}"
        for prior in request.relevant_outcomes
    )
    lines.extend(_states("Post-response health detector states", closure.final_detector_states))
    lines.extend(_states("Post-response incident detector states", closure.incident_detector_states))
    return _clip("\n".join(lines), _FINDINGS_BUDGET)


def _result_section(result: IncidentResult | None, dispatch_error: str | None) -> str:
    lines = ["## Responder structured result"]
    if result is None:
        lines.append(f"No responder result was recorded (dispatch error: {dispatch_error or 'none'}).")
        return "\n".join(lines)
    lines.append(f"Status: {result.status.value}" + (f"; error: {result.error}" if result.error else ""))
    lines.append("Confirmed root causes:")
    lines.extend(
        f"- {cause.summary} [{'; '.join(_ref(ref) for ref in cause.resources)}]"
        for cause in result.confirmed_root_causes
    )
    if not result.confirmed_root_causes:
        lines.append("- none")
    lines.append("Repair actions:")
    lines.extend(
        f"- [{'ok' if action.success else 'FAILED'}] {action.kind} {action.target}: {action.summary} -- "
        f"{_clip(action.details, _DETAILS_MAX_CHARS)}"
        for action in result.repair_actions
    )
    if not result.repair_actions:
        lines.append("- none recorded")
    if result.repair_changes:
        lines.append("Source changes: " + ", ".join(result.repair_changes))
    lines.append("Verification:")
    lines.extend(
        f"- [{'passed' if evidence.passed else 'FAILED'}] {evidence.name}: "
        f"{_clip(evidence.details, _DETAILS_MAX_CHARS)}"
        for evidence in result.verification_evidence
    )
    if not result.verification_evidence:
        lines.append("- none recorded")
    for playbook in result.applied_playbooks:
        scripts = f" (scripts: {', '.join(playbook.scripts)})" if playbook.scripts else ""
        lines.append(f"Applied playbook: {playbook.path}{scripts}")
    return _clip("\n".join(lines), _RESULT_BUDGET)


def responder_shell_commands(log: Path | None, session_id: str | None) -> list[str] | None:
    """Shell commands the responder session ran, from the per-turn usage log; ``None`` when unavailable."""

    if log is None or not session_id:
        return None
    commands: list[str] = []
    found = False
    try:
        with log.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("session_id") != session_id:
                    continue
                found = True
                lines = record.get("shell_command_lines")
                if isinstance(lines, list):
                    commands.extend(str(command) for command in lines)
    except OSError:
        return None
    return commands if found else None


def _commands_section(commands: list[str] | None) -> str:
    header = "## Responder shell commands"
    if commands is None:
        return f"{header}\nThe responder's shell commands are not available for this session."
    if not commands:
        return f"{header}\nThe responder session recorded no shell commands."
    rendered = [f"{index}. {_clip(command, _COMMAND_MAX_CHARS)}" for index, command in enumerate(commands, 1)]
    if sum(len(line) + 1 for line in rendered) <= _COMMANDS_BUDGET:
        return "\n".join([f"{header} ({len(commands)}, in order)", *rendered])
    # Keep the first diagnostic commands and as many of the last (repair and
    # verification) commands as fit.
    head = rendered[:_HEAD_COMMANDS]
    used = sum(len(line) + 1 for line in head)
    tail: list[str] = []
    for line in reversed(rendered[_HEAD_COMMANDS:]):
        if used + len(line) + 1 > _COMMANDS_BUDGET - 80:
            break
        tail.insert(0, line)
        used += len(line) + 1
    omitted = len(rendered) - len(head) - len(tail)
    return "\n".join(
        [
            f"{header} ({len(commands)}, in order)",
            *head,
            f"[... {omitted} commands omitted ...]",
            *tail,
        ]
    )


def _relevant_playbooks(closure: BrokerClosure, fired_detectors: set[str], possible: dict[str, list[str]]) -> list[str]:
    paths: list[str] = []
    candidates = [playbook.path for playbook in closure.request.surfaced_playbooks]
    for finding in closure.request.findings:
        candidates.extend(finding.playbooks)
    if closure.result is not None:
        candidates.extend(playbook.path for playbook in closure.result.applied_playbooks)
    for detector_id in sorted(fired_detectors):
        candidates.extend(possible.get(detector_id, []))
    for path in candidates:
        if path not in paths:
            paths.append(path)
    return paths


def _detector_detail(worktree: Path, package: str) -> list[str]:
    """Spec description and a bounded excerpt of the match predicate of one incident detector."""

    source = worktree / ".sdo" / "diagnostics" / package / "detector.go"
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["    (detector source unreadable)"]
    lines = []
    described = _DESCRIPTION_RE.search(text)
    if described:
        description = re.sub(r'"\s*\+\s*"', "", described.group(1)).strip()
        lines.append(f"    description: {_clip(description, _DETECTOR_DESCRIPTION_CHARS)}")
    else:
        lines.append("    description: (none in Spec())")
    lines.append(
        "    reports parameter bindings: " + ("yes" if "ParameterBindings" in text else "no (none set by the detector)")
    )
    detect = _DETECT_FUNC_RE.search(text)
    excerpt = text[detect.start() :] if detect else text
    lines.append(f"    match predicate excerpt ({source.relative_to(worktree).as_posix()}, Detect):")
    lines.append(_fenced(_clip(excerpt.strip(), _DETECTOR_PREDICATE_CHARS)))
    return lines


def _memory_section(worktree: Path, closure: BrokerClosure, *, detector_detail: bool = False) -> str:
    lines = ["## Existing operational memory (paths and short excerpts; open a file for its full text)"]
    memory = worktree / ".sdo"
    fired = {finding.detector_id for finding in closure.request.findings}
    possible: dict[str, list[str]] = {}
    try:
        manifest = MemoryRepository(worktree).diagnostics()
    except (MemoryRepositoryError, ValueError) as exc:
        lines.append(f"Detector manifest unreadable: {_clip(str(exc), 300)}")
    else:
        possible = {detector.id: list(detector.possible_playbooks) for detector in manifest.detectors}
        incident = [detector for detector in manifest.detectors if detector.detector_class == "incident"]
        health = [detector.id for detector in manifest.detectors if detector.detector_class == "health"]
        lines.append("Health detectors (judge-owned, never edit): " + (", ".join(health) or "none"))
        lines.append(
            "Incident detectors in .sdo/diagnostics/manifest.yaml:" if incident else "Incident detectors: none"
        )
        for detector in incident:
            watches = ", ".join(f"{watch.api_version}/{watch.kind}" for watch in detector.watches) or "none"
            marker = " [raised a finding in this incident]" if detector.id in fired else ""
            lines.append(
                f"- {detector.id} (package {detector.package}, watches: {watches}, firing: "
                f"{detector.persistence.firing}, playbooks: {detector.possible_playbooks or []}, "
                f"originatingIncident: {detector.originating_incident}){marker}"
            )
            if detector_detail:
                lines.extend(_detector_detail(worktree, detector.package))
    index = memory / "playbooks" / "README.md"
    if index.is_file():
        lines.append("Playbook index .sdo/playbooks/README.md:")
        lines.append(_fenced(_clip(index.read_text(encoding="utf-8", errors="replace").strip(), _INDEX_EXCERPT_CHARS)))
    existing = sorted(path.relative_to(worktree).as_posix() for path in memory.glob("playbooks/*/README.md"))
    lines.append("Existing playbooks: " + (", ".join(existing) or "none"))
    relevant = [path for path in _relevant_playbooks(closure, fired, possible) if path in existing]
    if not relevant:
        relevant = existing
    for path in relevant[:_MAX_PLAYBOOK_EXCERPTS]:
        text = (worktree / path).read_text(encoding="utf-8", errors="replace").strip()
        lines.append(f"Excerpt of {path}:")
        lines.append(_fenced(_clip(text, _PLAYBOOK_EXCERPT_CHARS)))
    return _clip("\n".join(lines), _MEMORY_BUDGET)


def incident_brief(
    closure: BrokerClosure,
    *,
    worktree: Path,
    responder_turn_log: Path | None = None,
    detector_detail: bool = False,
) -> str:
    """Render the bounded incident brief for a fresh first reflection attempt.

    ``detector_detail`` adds, for every existing incident detector, its Spec
    description and a bounded excerpt of its match predicate, so the reader can
    compare the confirmed signature with what each detector already matches.
    """

    session_id = None if closure.result is None else closure.result.responder_session_id
    sections = [
        _findings_section(closure),
        _result_section(closure.result, closure.dispatch_error),
        _memory_section(worktree, closure, detector_detail=detector_detail),
        _commands_section(responder_shell_commands(responder_turn_log, session_id)),
    ]
    body = _clip("\n\n".join(sections), BRIEF_MAX_CHARS)
    return (
        f"Incident brief (built by the broker from the verified closure; about {len(body) // 4} tokens):\n"
        f"{body}\nEnd of incident brief.\n"
    )
