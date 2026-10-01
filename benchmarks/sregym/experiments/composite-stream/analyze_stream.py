"""Per-position and per-sequence tables for the composite stream (sequences cstream-*).

    PYTHONPATH=. uv run python benchmarks/sregym/experiments/composite-stream/analyze_stream.py cstream-a cstream-b ...

Writes /mnt/data/shli/clc-runs/cstream-agg.json and prints markdown tables.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from benchmarks.sregym.analysis.composite_sequence import _targets, load_firings, memory_counts
from benchmarks.sregym.analysis.composite_table import rows
from benchmarks.sregym.fastloop.fault_tracker import composite_faults

R = Path("/mnt/data/shli/clc-runs")
SHORT = {
    "composite3_hotel_geo_rate_recommendation": "C1",
    "composite3b_hotel_profile_mongodb_geo_recommendation": "C2",
    "composite5_hotel_geo_rate_recommendation_frontend_user": "C3",
    "composite3c_hotel_rate_mongodb_geo_user": "C4",
    "composite4_hotel_profile_rate_recommendation_frontend": "C5",
}
GATE_MSG = "failed isolated validation and must be regenerated"


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def commits(ws: Path) -> list[tuple[str, int]]:
    out = subprocess.run(["git", "-C", str(ws), "log", "--format=%H %ct"], capture_output=True, text=True).stdout.splitlines()
    return [(line.split()[0], int(line.split()[1])) for line in out if line.strip()]


def gate_events(d: Path) -> list[tuple[float, bool]]:
    """(epoch, is_baseline_gate) for each distinct rejection line in the sequence's controller logs."""
    lines: set[str] = set()
    for log in d.glob("results/*/*/sdo_runtime/controller_logs/*.log"):
        lines |= set(log.read_text(errors="replace").splitlines())
    ordered = sorted(lines)
    events = []
    for line in ordered:
        if GATE_MSG not in line:
            continue
        stamp = line.split(" ", 1)[0]
        try:
            t = ts(stamp[:19] + "+00:00")
        except ValueError:
            continue
        gate = any("healthy_baseline_test.go" in x and x.split(" ", 1)[0][:19] == stamp[:19] for x in ordered)
        events.append((t, gate))
    return events


def analyse(name: str) -> list[dict]:
    d = R / name
    ws = d / "application_workspace"
    cm = commits(ws) if ws.exists() else []
    runs = [r for r in sorted((d / "results").glob(f"{name}-C*"), key=lambda p: int(p.name.rsplit("-C", 1)[1]))
            if (r / "incidents.jsonl").is_file() and list(r.glob("composite_*.json"))]
    starts = [ts(json.loads((r / "incidents.jsonl").read_text().splitlines()[0])["injection_started_at"]) for r in runs]
    gates = gate_events(d)
    out = []
    for k, r in enumerate(runs):
        row = rows([r])[0]
        inc = json.loads((r / "incidents.jsonl").read_text().splitlines()[0])
        comp = json.loads(next(r.glob("composite_*.json")).read_text())
        comps = {f.component for f in composite_faults(row["problem"])}
        end = starts[k + 1] if k + 1 < len(starts) else 1e18
        fir = load_firings(r, since=inc["injection_started_at"])
        learned_before: set[str] = set()
        offtarget: set[str] = set()
        learned_any: set[str] = set()
        for f in fir:
            if f.get("detector_class") == "health" or f.get("event") not in {"activated", "batched"}:
                continue
            tg = _targets(f)
            learned_any.add(f["detector_id"])
            if tg & comps:
                if f.get("dispatch_relation") in {"before_dispatch", "no_incident"}:
                    learned_before |= tg & comps
            else:
                offtarget.add(f["detector_id"])
        post = [c for c, t in cm if t < end]
        mem = memory_counts(ws, post[0]) if post else None
        g = [b for t, b in gates if starts[k] <= t < end]
        idx = int(r.name.rsplit("-C", 1)[1])
        out.append(dict(
            seq=name, pos=idx, C=SHORT.get(row["problem"], row["problem"]), resolved=row["resolved"], oracle=row["oracle_success"],
            all_s=row["all_resolved_s"], inj_mit=inc.get("injection_to_mitigation_seconds"), tokens=row["tokens"],
            followups=max(0, len(comp.get("incidents", [])) - 1), stop=row["stop_reason"], inert=row["inert_faults"],
            learned_before=sorted(learned_before), learned_fired=sorted(learned_any), offtarget=sorted(offtarget),
            mem=mem, gate_rejections=sum(1 for b in g if b), other_rejections=sum(1 for b in g if not b), error=row["error"],
            per={k2: v for k2, v in (row["per_fault_s"] or {}).items()},
        ))
    return out


def fmt(v, scale=1.0, nd=0) -> str:
    return "-" if v is None else f"{v / scale:.{nd}f}"


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def main(names: list[str]) -> None:
    allruns = [r for n in names if (R / n).exists() for r in analyse(n)]
    json.dump(allruns, open(R / "cstream-agg.json", "w"), indent=1)
    print("| seq | pos | C | solved | oracle | last-fault s | inj->mit s | tokens | follow-ups | learned detectors before dispatch (fault components) | learned fired off-target | gate rejections | memory inc.det/playbooks after | inert | stop/err |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for a in allruns:
        mem = f"{a['mem']['incident_detectors']}/{a['mem']['playbooks']}" if a["mem"] else "-"
        print(f"| {a['seq']} | {a['pos']} | {a['C']} | {a['resolved']} | {a['oracle']} | {fmt(a['all_s'])} | {fmt(a['inj_mit'])} | {a['tokens']/1e6:.2f}M | {a['followups']} | {', '.join(a['learned_before']) or 'none'} | {', '.join(a['offtarget']) or 'none'} | {a['gate_rejections']} (+{a['other_rejections']} other) | {mem} | {', '.join(a['inert']) or '-'} | {a['stop'] or ''} {a['error'] or ''} |")
    print()
    print("| pos | composite | n | all solved (probes) | oracle True | median tokens | median inj->mit s | median last-fault s | median follow-ups | median memory det/pb after |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for pos in sorted({a["pos"] for a in allruns}):
        g = [a for a in allruns if a["pos"] == pos]
        solved = sum(1 for a in g if a["resolved"].split("/")[0] == a["resolved"].split("/")[1])
        orc = sum(1 for a in g if a["oracle"] is True)
        md = med([a["mem"]["incident_detectors"] for a in g if a["mem"]]); mp = med([a["mem"]["playbooks"] for a in g if a["mem"]])
        print(f"| {pos} | {'/'.join(sorted({a['C'] for a in g}))} | {len(g)} | {solved}/{len(g)} | {orc}/{len(g)} | {fmt(med([a['tokens'] for a in g]), 1e6, 2)}M | {fmt(med([a['inj_mit'] for a in g]))} | {fmt(med([a['all_s'] for a in g]))} | {fmt(med([a['followups'] for a in g]), 1, 1)} | {fmt(md, 1, 1)}/{fmt(mp, 1, 1)} |")
    print()
    print("| seq | cumulative tokens (M) after pos 1..N | cumulative inj->mit min |")
    print("|---|---|---|")
    for n in names:
        g = sorted((a for a in allruns if a["seq"] == n), key=lambda a: a["pos"])
        ct = 0.0; cs = 0.0; ts_ = []; ss = []
        for a in g:
            ct += a["tokens"]; cs += a["inj_mit"] or 0; ts_.append(f"{ct/1e6:.1f}"); ss.append(f"{cs/60:.0f}")
        print(f"| {n} | {' '.join(ts_)} | {' '.join(ss)} |")
    print()
    print("| composite | first occurrence tokens (median) | repeat tokens (median) | first inj->mit | repeat inj->mit |")
    print("|---|---|---|---|---|")
    for c in sorted({a["C"] for a in allruns}):
        first, rep = [], []
        for n in names:
            seen = False
            for a in sorted((x for x in allruns if x["seq"] == n), key=lambda x: x["pos"]):
                if a["C"] != c:
                    continue
                (rep if seen else first).append(a)
                seen = True
        print(f"| {c} | {fmt(med([a['tokens'] for a in first]), 1e6, 2)}M (n={len(first)}) | {fmt(med([a['tokens'] for a in rep]), 1e6, 2)}M (n={len(rep)}) | {fmt(med([a['inj_mit'] for a in first]))} | {fmt(med([a['inj_mit'] for a in rep]))} |")


if __name__ == "__main__":
    main(sys.argv[1:])
