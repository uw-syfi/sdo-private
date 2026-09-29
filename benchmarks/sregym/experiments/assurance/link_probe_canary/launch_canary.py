"""Launch one link-probe-canary pipeline on an assure lane with the phase-1 safety nets.

Usage (repo root of the canary worktree): uv run python <this> <lane-offset> <config.toml> [--dry]

Gates on the launcher's own QuotaGate (stop line 96%), runs the enforcing preflight for the
lane, starts `benchmarks.sregym.run <config>` in its own session and registers the PID and its
planned tokens in the phase-1 launcher's resumed_pids.json / lane_budgets.json, which
quota_watchdog.py (96% global stop, 1.5x per-lane cap) reads.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import tomllib

from benchmarks.sregym.assurance import phase1_budget as budget
from benchmarks.sregym.assurance.phase1_launch import QuotaGate, SystemQuotaReader, lane_env

ROOT = Path.cwd()
LAUNCH = Path(
    "/mnt/data/shli/sdo-private/.claude/worktrees/agent-abba78c07e386ad4a"
    "/benchmarks/sregym/experiments/assurance/phase1/.launch"
)
LOGS = Path("/mnt/data/shli/assure-runs/lp1-logs")
STOP = 96.0

offset, config = sys.argv[1], Path(sys.argv[2]).resolve()
dry = "--dry" in sys.argv
lane = f"assure-w{offset}"
plan = tomllib.loads(config.read_text())
problems = [p for stage in plan["stages"] for p in stage["runner"]["problems"]]
seeded = "workspace_seed" in plan["pipeline"]
planned_tokens = sum(budget.sdo_stage_tokens(p) for p in problems) + (
    0 if seeded else budget.SDO_LIFECYCLE_BOOTSTRAP_TOKENS
)
planned_percent = planned_tokens * budget.POINTS_PER_TOKEN
gate = QuotaGate(stop_percent=STOP)
used = SystemQuotaReader().used_percent()
decision = {
    "canary": config.name,
    "lane": lane,
    "at": time.time(),
    "used_percent_at_start": used,
    "planned_nominal_tokens": planned_tokens,
    "planned_nominal_percent": planned_percent,
    "stop_percent": STOP,
    "decision": "start" if gate.can_start_matrix(used, planned_percent) else "abort_quota_start",
}
print("QuotaGate:", json.dumps(decision, indent=2))
with (LAUNCH / "gate_decision_exec.jsonl").open("a") as stream:
    stream.write(json.dumps(decision) + "\n")
if decision["decision"] != "start" or dry:
    sys.exit(0 if dry else "gate refused the start")

env = lane_env(lane, gate)
# Host-side lifecycle validation would otherwise use the shared :v0.1.0 validator, which predates the link detector.
env["SDO_VALIDATOR_IMAGE"] = "sdo-detector-validator:lp1"
LOGS.mkdir(parents=True, exist_ok=True)
log = LOGS / f"{lane}.{config.stem}.log"
with log.open("ab") as stream:
    proc = subprocess.Popen(
        [sys.executable, "-m", "benchmarks.sregym.run", str(config)],
        cwd=ROOT,
        env=env,
        stdout=stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
label = f"{lane} lp-canary {config.name}"
for name, entry in (
    ("resumed_pids.json", proc.pid),
    ("lane_budgets.json", {"log": str(log), "planned_tokens": round(planned_tokens), "pid_labels": [label]}),
):
    path = LAUNCH / name
    data = json.loads(path.read_text()) if path.exists() else {}
    data[label] = entry
    path.write_text(json.dumps(data, indent=2) + "\n")
print(f"started {label} pid {proc.pid} at {time.strftime('%H:%M:%S', time.gmtime())}Z log {log}", flush=True)
