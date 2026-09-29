"""Per-stage SDO timelines and exec-usage counts for the phase-1 report.

Two things :mod:`benchmarks.sregym.assurance.phase1_analyze` does not expose:

* a timeline of one SDO stage (injection, first detector finding, incident
  open, responder session start, first agent action, last mitigating
  mutation, gate clear), so a repeat's TTM can be split into fixed overhead
  (detection, Job start) and agent time; and
* how often an agent ran ``kubectl exec``, ``attach`` or ``port-forward``.

Usage::

    uv run python -m benchmarks.sregym.assurance.phase1_timeline \\
        --sdo <pipeline dirs...> --codex <codex dirs...> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.sregym.analysis.incident_cost import (
    _is_state_change,
    _rollout_commands,
    _timestamp,
    last_mutation_done_at,
    load_codex_runs,
    pipeline_stage_dirs,
    problem_results_dirs,
    read_result_row,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG_LINE = re.compile(r"^(\S+Z) (\{.*)$")
_EXEC_VERBS = {"exec": "exec_calls", "attach": "attach_calls", "port-forward": "port_forward_calls"}


@dataclass(frozen=True)
class ExecUsage:
    exec_calls: int = 0
    attach_calls: int = 0
    port_forward_calls: int = 0
    total_calls: int = 0

    @property
    def used(self) -> bool:
        return bool(self.exec_calls or self.attach_calls or self.port_forward_calls)


def _kubectl_verb(segment: str) -> str | None:
    tokens = segment.split()
    if not tokens or Path(tokens[0]).name != "kubectl":
        return None
    rest = iter(tokens[1:])
    for token in rest:
        if token in {"-n", "--namespace", "--context", "--kubeconfig"}:
            next(rest, None)
        elif not token.startswith("-"):
            return token
    return None


def count_exec_usage(rollouts: Sequence[Path]) -> ExecUsage:
    """Count tool calls that ran kubectl exec, attach or port-forward (one per call)."""

    counts = {"exec_calls": 0, "attach_calls": 0, "port_forward_calls": 0}
    total = 0
    for path in rollouts:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"function_call"' not in line and '"custom_tool_call"' not in line:
                continue
            payload = json.loads(line).get("payload")
            if not isinstance(payload, dict) or payload.get("type") not in {"function_call", "custom_tool_call"}:
                continue
            total += 1
            verbs = {
                _EXEC_VERBS[verb]
                for command in _rollout_commands(payload)
                for segment in re.split(r"&&|\|\||[;|\n]", command)
                if (verb := _kubectl_verb(segment)) in _EXEC_VERBS
            }
            for key in verbs:
                counts[key] += 1
    return ExecUsage(total_calls=total, **counts)


def incident_open_epoch(incident_id: str) -> float:
    """The incident id ends in the open time in epoch nanoseconds."""

    return int(incident_id.rsplit("-", 1)[1]) / 1e9


@dataclass(frozen=True)
class Finding:
    epoch: float
    detector_id: str
    rule_id: str


def first_finding_after(logs: Sequence[Path], *, after: float) -> Finding | None:
    """First active detector finding in controller logs at or after *after* (epoch seconds)."""

    best: Finding | None = None
    for path in logs:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _LOG_LINE.match(line)
            if not match or '"findings":[{' not in match.group(2):
                continue
            stamp = _timestamp(match.group(1)[:26] + "Z" if "." in match.group(1) else match.group(1))
            if stamp is None or stamp < after or (best is not None and stamp >= best.epoch):
                continue
            try:
                findings = json.loads(match.group(2)).get("findings") or []
            except json.JSONDecodeError:
                continue
            active = [f for f in findings if f.get("status") == "active"]
            if active:
                best = Finding(stamp, str(active[0].get("detector_id")), str(active[0].get("rule_id")))
    return best


@dataclass(frozen=True)
class StageTimeline:
    stage: str
    problem_id: str
    incident_id: str | None
    inject_s: float
    detect_s: float | None
    detector: str | None
    open_s: float | None
    session_start_s: float | None
    first_action_s: float | None
    last_mutation_s: float | None
    gate_clear_s: float | None
    diagnosis_post_s: float | None
    mitigation_post_s: float | None
    applied_playbook_count: int | None
    warm_path: bool | None
    playbook_read: bool
    repair_script_run: bool
    responder_commands: int
    exec_usage: ExecUsage

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _receipt(results: Path) -> dict[str, Any]:
    for name in ("sdo_production_receipt_strict.json", "sdo_rejected_production_receipt.json"):
        for path in sorted(results.rglob(name)):
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("receipt", data)
    for path in sorted(results.rglob("sdo_incident_resolution.json")):
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _rollout_calls(path: Path) -> list[tuple[float, list[str]]]:
    calls = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"function_call"' not in line and '"custom_tool_call"' not in line:
            continue
        record = json.loads(line)
        payload = record.get("payload")
        stamp = _timestamp(record.get("timestamp"))
        if isinstance(payload, dict) and stamp is not None and payload.get("type") in {"function_call", "custom_tool_call"}:
            calls.append((stamp, _rollout_commands(payload)))
    return calls


def stage_timeline(stage: str, problem_id: str, results: Path) -> StageTimeline | None:
    row = read_result_row(results) or {}
    injected = _num(row.get("fault_injected_at"))
    if injected is None:
        return None
    receipt = _receipt(results)
    session = receipt.get("responder_session_id")
    rollouts = [
        p
        for p in sorted(results.rglob("sdo_runtime/codex/sessions/**/rollout-*.jsonl"))
        if isinstance(session, str) and session in p.name
    ]
    logs = sorted(results.rglob("sdo_runtime/controller_logs/*.log"))
    incident_id = receipt.get("incident_id") if isinstance(receipt.get("incident_id"), str) else None
    finding = first_finding_after(logs, after=injected)
    calls = [call for path in rollouts for call in _rollout_calls(path)]
    calls.sort(key=lambda item: item[0])
    session_start = None
    if rollouts:
        first_line = rollouts[0].read_text(encoding="utf-8", errors="replace").splitlines()[0]
        session_start = _timestamp(json.loads(first_line).get("timestamp"))
    submitted = _num(row.get("mitigation_submitted_at"))
    last_mut = (
        last_mutation_done_at(rollouts, after=injected, before=submitted) if rollouts and submitted is not None else None
    )
    verified = _timestamp(receipt.get("verified_at")) if receipt.get("verified_at") else None
    reuse = receipt.get("memory_reuse") if isinstance(receipt.get("memory_reuse"), dict) else {}
    commands = [command for _, cmds in calls for command in cmds]

    def rel(epoch: float | None) -> float | None:
        return None if epoch is None else round(epoch - injected, 2)

    diagnosis_at = _num(row.get("diagnosis_submitted_at"))
    return StageTimeline(
        stage=stage,
        problem_id=problem_id,
        incident_id=incident_id,
        inject_s=0.0,
        detect_s=rel(finding.epoch if finding else None),
        detector=f"{finding.detector_id}/{finding.rule_id}" if finding else None,
        open_s=rel(incident_open_epoch(incident_id) if incident_id else None),
        session_start_s=rel(session_start),
        first_action_s=rel(calls[0][0] if calls else None),
        last_mutation_s=rel(last_mut),
        gate_clear_s=rel(verified),
        diagnosis_post_s=rel(diagnosis_at),
        mitigation_post_s=rel(submitted),
        applied_playbook_count=reuse.get("applied_playbook_count") if reuse else None,
        warm_path=reuse.get("warm_path") if reuse else None,
        playbook_read=any("playbook" in c.lower() for c in commands),
        repair_script_run=any(_is_state_change(c) and "repair" in c for c in commands),
        responder_commands=len(commands),
        exec_usage=count_exec_usage(rollouts),
    )


def _num(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def sdo_timelines(pipeline_dir: Path) -> list[StageTimeline]:
    out = []
    for _, name, stage_dir in pipeline_stage_dirs(pipeline_dir):
        for problem_id, results in problem_results_dirs(stage_dir):
            timeline = stage_timeline(name, problem_id, results)
            if timeline is not None:
                out.append(timeline)
    return out


def codex_exec_usage(directory: Path) -> list[tuple[str, ExecUsage]]:
    out = []
    for run in load_codex_runs(directory):
        rollouts = sorted(Path(run.source).rglob("sessions/**/rollout-*.jsonl"))
        out.append((run.problem_id, count_exec_usage(rollouts)))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--sdo", type=Path, nargs="+", default=[])
    parser.add_argument("--codex", type=Path, nargs="+", default=[])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    result = {
        "sdo": {str(d): [t.to_json() for t in sdo_timelines(d)] for d in args.sdo},
        "codex_exec": {str(d): [(p, asdict(u)) for p, u in codex_exec_usage(d)] for d in args.codex},
    }
    text = json.dumps(result, indent=1)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
