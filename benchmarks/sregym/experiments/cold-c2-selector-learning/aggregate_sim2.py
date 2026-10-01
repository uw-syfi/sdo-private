import json
import subprocess
from datetime import datetime
from pathlib import Path

from benchmarks.sregym.analysis.composite_sequence import (
    first_activations,
    learned_before_dispatch,
    load_firings,
    memory_counts,
)
from benchmarks.sregym.analysis.composite_table import rows
from benchmarks.sregym.fastloop.fault_tracker import composite_faults

R = Path("/mnt/data/shli/clc-runs")
SEQS = {
    "pf-a": "sequential",
    "pf-b": "sequential",
    "sim-a": "simultaneous",
    "sim-b": "simultaneous",
    "sim-c": "simultaneous",
    "sim-d": "simultaneous",
    "cc2-a": "cold-C2",
    "cc2-b": "cold-C2",
    "cc2-c": "cold-C2",
    "cc2-d": "cold-C2",
    "sel-a": "selector",
    "sel-b": "selector",
    "sel-c": "selector",
    "sel2-a": "selector-reset",
    "sel2-b": "selector-reset",
    "sel2-c": "selector-reset",
    "sel3-a": "selector-labelreset",
    "sel3-b": "selector-labelreset",
    "sel3-c": "selector-labelreset",
    "selw-a": "seeded-selector",
    "selw-b": "seeded-selector",
    "selw-c": "seeded-selector",
}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def commits(ws):
    out = subprocess.run(["git", "-C", str(ws), "log", "--format=%H %ct"], capture_output=True, text=True).stdout.split(
        "\n"
    )
    return [(l.split()[0], int(l.split()[1])) for l in out if l.strip()]


lines = []
agg = {}
for name, arm in SEQS.items():
    d = R / name
    if not d.exists():
        continue
    ws = d / "application_workspace"
    cm = commits(ws)
    runs = [
        r
        for r in sorted((d / "results").glob(f"{name}-C*"))
        if (r / "incidents.jsonl").is_file() and list(r.glob("composite_*.json"))
    ]
    starts = []
    for r in runs:
        inc = json.loads((r / "incidents.jsonl").read_text().splitlines()[0])
        starts.append(ts(inc["injection_started_at"]))
    for k, r in enumerate(runs):
        tb = rows([r])
        if not tb:
            continue
        row = tb[0]
        inc = json.loads((r / "incidents.jsonl").read_text().splitlines()[0])
        firings = load_firings(r, since=inc["injection_started_at"])
        faults = composite_faults(row["problem"])
        lbd = {
            f.name: (
                "before"
                if learned_before_dispatch(firings, f.component)
                else ("after" if "learned" in first_activations(firings, f.component) else "none")
            )
            for f in faults
        }
        end = starts[k + 1] if k + 1 < len(starts) else 1e18
        pre = [c for c, t in cm if t < starts[k]]
        post = [c for c, t in cm if t < end]
        mem_pre = memory_counts(ws, pre[0]) if pre else None
        mem_post = memory_counts(ws, post[0]) if post else None
        # n responders
        agg_row = dict(
            seq=name,
            arm=arm,
            C=r.name.split("-")[-1],
            problem=row["problem"],
            resolved=row["resolved"],
            all_s=row["all_resolved_s"],
            oracle=row["oracle_success"],
            tokens=row["tokens"],
            stop=row["stop_reason"],
            per=row["per_fault_s"],
            lbd=lbd,
            mem_pre=mem_pre,
            mem_post=mem_post,
            err=row["error"],
            wall=inc.get("incident_wall_seconds"),
        )
        lines.append(agg_row)
json.dump(lines, open(R / "agg_cmp2.json", "w"), indent=1)


def f(v):
    return "-" if v is None else f"{v:.0f}"


print(
    "| seq | arm | C | resolved | all-resolved s | oracle | tokens | per-fault s | learned detector before dispatch (per fault) | memory (inc. detectors/playbooks) before -> after |"
)
print("|---|---|---|---|---|---|---|---|---|---|")
for a in lines:
    per = ", ".join(f"{k.split(':')[0]}:{k.split(':')[1]} {f(v)}" for k, v in a["per"].items())
    lb = ", ".join(f"{k.split(':')[1]} {v}" for k, v in a["lbd"].items())
    mp, mq = a["mem_pre"], a["mem_post"]
    mem = (
        f"{mp['incident_detectors']}/{mp['playbooks']} -> {mq['incident_detectors']}/{mq['playbooks']}"
        if mp and mq
        else "-"
    )
    print(
        f"| {a['seq']} | {a['arm']} | {a['C']} | {a['resolved']} | {f(a['all_s'])} | {a['oracle']} | {a['tokens'] / 1e6:.2f}M | {per} | {lb} | {mem} |"
        + (f" {a['err']}" if a["err"] else "")
    )

print()
print(
    "| seq | C | inj->mit s | follow-up incidents | stop | distinct findings activated before dispatch (health/learned) | learned detectors fired (any time) |"
)
print("|---|---|---|---|---|---|---|")
for r in sorted(p for n in SEQS for p in (R).glob(f"{n}/results/{n}-C*")):
    comp = list(r.glob("composite_*.json"))
    if not comp or not (r / "incidents.jsonl").is_file():
        continue
    c = json.loads(comp[0].read_text())
    inc = json.loads((r / "incidents.jsonl").read_text().splitlines()[0])
    fs = load_firings(r, since=inc["injection_started_at"])
    fired_before = {"health": set(), "learned": set()}
    anyl = set()
    for firing in fs:
        k = "health" if firing.get("detector_class") == "health" else "learned"
        if firing["event"] in ("activated", "batched") and firing.get("dispatch_relation") in (
            "before_dispatch",
            "no_incident",
        ):
            fired_before[k].add(firing["fingerprint"])
        if k == "learned" and firing["event"] in ("activated", "batched"):
            anyl.add(firing["detector_id"])
    m = inc.get("injection_to_mitigation_seconds")
    print(
        f"| {r.parent.parent.name} | {r.name.split('-')[-1]} | {m and round(m)} | {len(c['incidents']) - 1} | {c['stop_reason']} | {len(fired_before['health'])}/{len(fired_before['learned'])} | {len(anyl)} |"
    )
