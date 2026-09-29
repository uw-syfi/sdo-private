"""Per-stage link-probe canary facts: findings, incident, responder actions, gate, times, tokens.

Usage (repo root): uv run python <this> <pipeline_dir>... [--json out.json]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from benchmarks.sregym.analysis.incident_cost import (
    _is_state_change,
    _rollout_commands,
    _timestamp,
    load_sdo_pipeline,
    pipeline_stage_dirs,
    problem_results_dirs,
    read_result_row,
)

_LOG = re.compile(r"^(\S+Z) (\{.*)$")


def _epoch(text: str) -> float:
    head, _, frac = text.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{head}.{frac[:6]}+00:00" if frac else f"{head}+00:00").timestamp()


def controller_events(logs: list[Path]) -> list[tuple[float, list[dict]]]:
    events = []
    for path in logs:
        for line in path.read_text(errors="replace").splitlines():
            match = _LOG.match(line)
            if not match or '"findings":[' not in match.group(2):
                continue
            try:
                findings = json.loads(match.group(2)).get("findings") or []
            except json.JSONDecodeError:
                continue
            events.append((_epoch(match.group(1)), findings))
    return sorted(events, key=lambda item: item[0])


def _num(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def analyze_stage(name: str, problem: str, results: Path) -> dict:
    row = read_result_row(results) or {}
    injected = _num(row.get("fault_injected_at"))
    receipt_path = next(iter(sorted(results.rglob("sdo_incident_resolution.json"))), None)
    receipt = json.loads(receipt_path.read_text()) if receipt_path else {}
    request = (receipt.get("fault_gate_timings_seconds") or {}).get("fault_injection_request") or 0.0
    base = (injected or 0.0) - request - 1.0
    events = controller_events(sorted(results.rglob("sdo_runtime/controller_logs/*.log")))
    first: dict[str, float] = {}
    last_active: dict[str, float] = {}
    for stamp, findings in events:
        if stamp < base:
            continue
        for finding in findings:
            if finding.get("status") != "active":
                continue
            key = f"{finding.get('detector_id')}|{finding.get('rule_id')}"
            first.setdefault(key, stamp)
            last_active[key] = stamp

    # Times are seconds from the START of the injection request (the harness stamps fault_injected_at when
    # the request returns, up to several seconds later for the composite fault).
    anchor = None if injected is None else injected - request

    def rel(epoch: float | None) -> float | None:
        return None if epoch is None or anchor is None else round(epoch - anchor, 1)

    link_keys = {k: v for k, v in first.items() if "|link-reachability." in k}
    session = receipt.get("responder_session_id")
    rollouts = [
        p
        for p in sorted(results.rglob("sdo_runtime/codex/sessions/**/rollout-*.jsonl"))
        if isinstance(session, str) and session in p.name
    ]
    commands: list[tuple[float, str]] = []
    for path in rollouts:
        for line in path.read_text(errors="replace").splitlines():
            if '"function_call"' not in line and '"custom_tool_call"' not in line:
                continue
            record = json.loads(line)
            payload = record.get("payload")
            stamp = _timestamp(record.get("timestamp"))
            if isinstance(payload, dict) and stamp is not None:
                commands.extend((stamp, c) for c in _rollout_commands(payload))
    commands.sort()
    mutations = [(rel(t), c[:220]) for t, c in commands if _is_state_change(c)]
    np_deleted = [
        (rel(t), c[:200])
        for t, c in commands
        if "deny-all-recommendation" in c and re.search(r"\b(delete|patch|replace|apply)\b", c)
    ]
    incident_id = receipt.get("incident_id")
    open_epoch = int(incident_id.rsplit("-", 1)[1]) / 1e9 if isinstance(incident_id, str) else None
    verified = _timestamp(receipt.get("verified_at")) if receipt.get("verified_at") else None
    gate_refusals = [rel(t) for t, c in commands if "incident status" in c or "incident close" in c]
    return {
        "stage": name,
        "problem": problem,
        "diagnosis_success": row.get("Diagnosis.success"),
        "mitigation_success": row.get("Mitigation.success"),
        "diagnosis_submitted_s": rel(_num(row.get("diagnosis_submitted_at"))),
        "mitigation_submitted_s": rel(_num(row.get("mitigation_submitted_at"))),
        "injection_request_s": round(request, 2),
        "first_findings_s": {k: rel(v) for k, v in sorted(first.items(), key=lambda kv: kv[1])},
        "link_findings_s": {k: rel(v) for k, v in link_keys.items()},
        "link_last_active_s": {k: rel(last_active[k]) for k in link_keys},
        "incident_open_s": rel(open_epoch),
        "incident_id": incident_id,
        "gate_clear_verified_at_s": rel(verified),
        "incident_resolution_seconds": receipt.get("incident_resolution_seconds"),
        "responder_commands": len(commands),
        "responder_mutations": mutations,
        "responder_touched_deny_all_recommendation": np_deleted,
        "incident_status_or_close_calls_s": gate_refusals,
        "confirmed_root_causes": [
            {
                "summary": c.get("summary"),
                "resources": [f"{r.get('kind')}/{r.get('name')}" for r in c.get("resources", [])],
            }
            for c in receipt.get("confirmed_root_causes", [])
        ],
    }


def main(argv: list[str]) -> int:
    out = None
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        argv = [a for a in argv if a not in ("--json", str(out))]
    result = {}
    for pipeline in map(Path, argv):
        stages = load_sdo_pipeline(pipeline)
        by_name = {s.name: s for s in stages}
        rows = []
        for _, name, stage_dir in pipeline_stage_dirs(pipeline):
            for problem, results in problem_results_dirs(stage_dir):
                facts = analyze_stage(name, problem, results)
                stage = by_name.get(name)
                if stage is not None:
                    v = stage.verdict
                    facts["ttd_s"] = v.diagnosis_seconds
                    facts["ttm_judge_free_s"] = v.ttm_seconds
                    facts["first_mutation_s"] = v.mitigation_applied_seconds
                    facts["last_mutation_s"] = v.last_mitigation_seconds
                    facts["responder_tokens"] = stage.responder.total()
                    facts["reflection_tokens"] = stage.reflection.total()
                    facts["lifecycle_tokens"] = stage.lifecycle.total()
                rows.append(facts)
        result[str(pipeline)] = rows
    text = json.dumps(result, indent=1, default=str)
    if out:
        out.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
