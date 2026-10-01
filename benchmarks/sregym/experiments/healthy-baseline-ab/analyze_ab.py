"""Per-sequence and per-arm table for the healthy-baseline gate A/B (sequences abon-* and aboff-*).

    uv run python -m benchmarks.sregym.experiments.healthy-baseline-ab.analyze_ab  (or run the file with PYTHONPATH=.)
"""
from __future__ import annotations

import json
from datetime import datetime
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from benchmarks.sregym.analysis.composite_sequence import _targets, load_firings
from benchmarks.sregym.analysis.composite_table import rows
from benchmarks.sregym.fastloop.fault_tracker import composite_faults

R = Path("/mnt/data/shli/clc-runs")
LABELS = {1: "C3", 2: "C3'", 3: "C1"}
REJECT = re.compile(r"healthy baseline|failed isolated validation|must be regenerated|selector-excludes|reported an active finding", re.I)


def git(ws: Path, *args: str) -> list[str]:
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True).stdout.splitlines()


def detectors_ever(ws: Path) -> dict[str, str]:
    """detector dir name -> commit that first added it (all history)."""
    out: dict[str, str] = {}
    cur = ""
    for line in git(ws, "log", "--reverse", "--diff-filter=A", "--name-only", "--format=@@%H", "--", ".sdo/diagnostics/detectors/incidents"):
        if line.startswith("@@"):
            cur = line[2:]
        elif line.count("/") >= 4:
            out.setdefault(line.split("/")[4], cur)
    return out


def analyse(name: str) -> dict:
    d = R / name
    ws = d / "application_workspace"
    created = detectors_ever(ws)
    first = sorted((d / "results").glob(f"{name}-C1"))
    t0 = 0.0
    if first and (first[0] / "incidents.jsonl").is_file():
        t0 = datetime.fromisoformat(json.loads((first[0] / "incidents.jsonl").read_text().splitlines()[0])["injection_started_at"].replace("Z", "+00:00")).timestamp()
    created = {n: c for n, c in created.items() if int(git(ws, "show", "-s", "--format=%ct", c)[0]) > t0}
    runs = []
    for r in sorted((d / "results").glob(f"{name}-C*")):
        if not (r / "incidents.jsonl").is_file() or not list(r.glob("composite_*.json")):
            continue
        row = rows([r])[0]
        inc = json.loads((r / "incidents.jsonl").read_text().splitlines()[0])
        idx = int(r.name.split("-C")[-1])
        comps = {f.component for f in composite_faults(row["problem"])}
        fir = load_firings(r, since=inc["injection_started_at"])
        det: dict[str, dict] = defaultdict(lambda: {"on": set(), "off": set(), "incidents": set(), "relation": set()})
        for f in fir:
            if f.get("detector_class") == "health" or f.get("event") not in {"activated", "batched"}:
                continue
            t = _targets(f)
            key = "on" if t & comps else "off"
            det[f["detector_id"]][key].add(f["fingerprint"])
            if f.get("incident_id"):
                det[f["detector_id"]]["incidents"].add(f["incident_id"])
            det[f["detector_id"]]["relation"].add(str(f.get("dispatch_relation")))
        rej = []
        for log in r.glob("*/sdo_runtime/controller_logs/*.log"):
            for line in log.read_text(errors="replace").splitlines():
                if REJECT.search(line):
                    rej.append(line[:600])
        rej += [l[:600] for p in r.glob("*/sdo_runtime/**/*rejected*") if p.is_file() for l in [p.read_text(errors="replace")] if REJECT.search(l)]
        runs.append(dict(
            seq=name, C=LABELS.get(idx, str(idx)), idx=idx, problem=row["problem"], resolved=row["resolved"], oracle=row["oracle_success"],
            all_s=row["all_resolved_s"], per={k.split(":")[1]: v for k, v in (row["per_fault_s"] or {}).items()}, tokens=row["tokens"],
            inj_mit=inc.get("injection_to_mitigation_seconds"), reflection_attempts=inc.get("reflection_attempts"), error=row["error"],
            fired={k: {"on": sorted(v["on"]), "off": sorted(v["off"]), "incidents": len(v["incidents"]), "relation": sorted(v["relation"])} for k, v in det.items()},
            rejections=rej,
        ))
    return dict(seq=name, created=created, runs=runs)


def fmt(v) -> str:
    return "-" if v is None else f"{v:.0f}"


def main(names: list[str]) -> None:
    results = [analyse(n) for n in names if (R / n / "application_workspace").exists()]
    json.dump(results, open(R / "ab-agg.json", "w"), indent=1, default=list)
    print("| seq | run | solved | oracle | last-fault s | inj->mit s | tokens | per-fault s | refl attempts | learned detectors fired: on-target / OFF-target (fingerprints) | gate/validation rejection lines |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in results:
        for r in s["runs"]:
            per = ", ".join(f"{k} {fmt(v)}" for k, v in r["per"].items())
            fired = "; ".join(f"{k}: on {len(v['on'])}, OFF {v['off'] or 0} (inc {v['incidents']}, {'/'.join(v['relation'])})" for k, v in r["fired"].items()) or "none"
            print(f"| {s['seq']} | {r['C']} | {r['resolved']} | {r['oracle']} | {fmt(r['all_s'])} | {fmt(r['inj_mit'])} | {r['tokens']/1e6:.2f}M | {per} | {r['reflection_attempts']} | {fired} | {len(r['rejections'])} |" + (f" {r['error']}" if r["error"] else ""))
    print()
    print("| seq | learned incident detectors created over the sequence (name <- first commit) |")
    print("|---|---|")
    for s in results:
        print(f"| {s['seq']} | " + ", ".join(f"{n}" for n in s["created"]) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
