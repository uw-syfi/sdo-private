"""Targeted gate replay: re-run the real reflection of one stored closure n times and measure the gate offline.

For each replay k the production reflector (fresh session, guidance ``generalize``, Codex gpt-6-luna, scratch clone at the
incident's outcome commit) proposes memory. The proposal is then checked with the detector validator's own entry point
twice: without the baseline gate (gate OFF: would the proposal be committed?) and with it (gate ON: is it rejected, and
does the reflector, given the same retry prompt as the broker, propose a valid detector within ``--max-retries``?).
The ON verdict of attempt 0 and the OFF verdict are computed on the same proposal (paired design).

    PYTHONPATH=. uv run python benchmarks/sregym/experiments/healthy-baseline-ab/gate_replay.py \
        --workspace /mnt/data/shli/clc-runs/sel3-b/application_workspace --incident-suffix 137584015 --k 0 --out DIR
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from benchmarks.sregym.analysis.reflection_replay import _git, _ScopedBackend, find_ledger, memory_delta
from sdo.agent_runtime.responder.reflection import INCIDENT_REASONING_EFFORT, SessionReflector
from sdo.operational_memory import BrokerClosure, OutcomeRecord

HEALTHY = Path("/mnt/data/shli/clc-runs/hb-live/healthy")


def check(clone: Path, *, baseline: bool) -> tuple[int, str]:
    staged = clone / ".sdo-baseline" / "healthy"
    cmd = [sys.executable, "-m", "controller.builder.check_cli", "test", "--app", str(clone)]
    if baseline:
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(HEALTHY, staged, dirs_exist_ok=True)
        cmd += ["--healthy-baseline", ".sdo-baseline/healthy"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(clone / ".sdo-baseline", ignore_errors=True)


def verdict(clone: Path) -> dict:
    off_rc, off_out = check(clone, baseline=False)
    on_rc, on_out = check(clone, baseline=True)
    findings = sorted({line.split('detector "', 1)[1].split('"', 1)[0] for line in on_out.splitlines() if "reported an active finding" in line})
    resources = sorted({line.split(" on ", 2)[-1].split(":")[0].split(" ", 1)[-1] for line in on_out.splitlines() if "reported an active finding" in line and " on Service " in line or " on Deployment " in line})
    return dict(
        off_pass=off_rc == 0,
        on_pass=on_rc == 0,
        gate_rejected=off_rc == 0 and on_rc != 0 and bool(findings),
        false_firing_detectors=findings,
        violation_lines=sum("reported an active finding" in line for line in on_out.splitlines()),
        resources=resources,
        off_output_tail=off_out[-600:] if off_rc else "",
        on_output=on_out if on_rc else "",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--incident-suffix", required=True)
    ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--model", default="gpt-6-luna")
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    ledger = find_ledger(a.workspace, a.incident_suffix)
    out = a.out / f"r{a.k}"
    clone = out / "clone"
    out.mkdir(parents=True, exist_ok=True)
    if clone.exists():
        raise SystemExit(f"{clone} exists")
    _git(out, "clone", "--quiet", "--no-hardlinks", str(a.workspace), str(clone))
    _git(clone, "checkout", "--quiet", "--detach", ledger["outcome_commit"])
    records = [OutcomeRecord.model_validate_json(l) for l in (clone / ".sdo" / "outcomes.jsonl").read_text().splitlines() if l.strip()]
    outcome = next(r for r in reversed(records) if r.incident_id == ledger["incident_id"])
    closure = BrokerClosure.model_validate(ledger["closure"])
    reflector = SessionReflector(_ScopedBackend(model=a.model, reasoning_effort=INCIDENT_REASONING_EFFORT, timeout_seconds=a.timeout), guidance="generalize")
    attempts = []
    feedback = None
    rejected_diff = None
    for attempt in range(a.max_retries + 1):
        turn = reflector.resume(
            session_id=str(ledger.get("responder_session_id") or ""), incident_id=outcome.incident_id, worktree=clone,
            outcome=outcome, history=records, outcome_commit=ledger["outcome_commit"], validation_feedback=feedback,
            rejected_proposal_diff=rejected_diff, session_mode="fresh", closure=closure,
        )
        delta = memory_delta(clone)
        rec = dict(attempt=attempt, learning_decision=turn.learning_decision, new_detectors=delta["new_detectors"], modified_detectors=delta["modified_detectors"], usage=turn.usage)
        touched = delta["new_detectors"] + delta["modified_detectors"]
        if touched:
            rec["verdict"] = verdict(clone)
        attempts.append(rec)
        (out / f"diff-{attempt}.patch").write_text(_git(clone, "diff", "HEAD") + _git(clone, "ls-files", "--others", "--exclude-standard"))
        v = rec.get("verdict")
        if not v or not v["gate_rejected"] or attempt == a.max_retries:
            break
        # broker rollback: keep the rejected diff for the retry prompt, reset the worktree to the outcome commit
        rejected_diff = _git(clone, "diff", "HEAD")
        feedback = f"reflection proposal failed isolated validation and must be regenerated: {v['on_output']}"
        _git(clone, "reset", "--hard", "--quiet", "HEAD")
        _git(clone, "clean", "-fdq")
    (out / "result.json").write_text(json.dumps(dict(k=a.k, incident=outcome.incident_id, attempts=attempts), indent=1))
    print(json.dumps(dict(k=a.k, attempts=[{k: v for k, v in x.items() if k != "usage"} | ({"verdict": {kk: vv for kk, vv in x["verdict"].items() if kk not in ("on_output", "off_output_tail")}} if x.get("verdict") else {}) for x in attempts]), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
