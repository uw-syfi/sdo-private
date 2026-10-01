import json,sys,subprocess
from pathlib import Path
from collections import defaultdict
from benchmarks.sregym.analysis.composite_sequence import load_firings
R=Path("/mnt/data/shli/clc-runs")
LABEL={"composite3b":"C2","composite5":"C3","composite3_":"C1"}
def lab(p,idx=1):
    if p.startswith("composite3b"): return "C2 (cold)"
    if p.startswith("composite5"): return "C3" if idx==1 else "C3' (repeat)"
    return "C1 (after C3,C3')"
def detectors(ws):
    out=subprocess.run(["git","-C",str(ws),"ls-tree","-r","--name-only","HEAD",".sdo/diagnostics/detectors/incidents"],capture_output=True,text=True).stdout
    return sorted({l.split("/")[4] for l in out.split() if l.count("/")>=4})
rows=[]
for name in sys.argv[1:]:
    ws=R/name/"application_workspace"
    for r in sorted((R/name/"results").glob(f"{name}-C*")):
        comp=list(r.glob("composite_*.json"))
        if not comp or not (r/"incidents.jsonl").is_file(): continue
        c=json.loads(comp[0].read_text()); inc=json.loads((r/"incidents.jsonl").read_text().splitlines()[0])
        idx=int(r.name.split("-C")[-1])
        fs=load_firings(r,since=inc["injection_started_at"])
        d=defaultdict(lambda:[set(),set()])
        for f in fs:
            if f.get("detector_class")=="health": continue
            if f["event"] in("activated","batched"):
                d[f["detector_id"]][0].add(f["fingerprint"]); d[f["detector_id"]][1].add(str(f.get("dispatch_relation")))
        learned=", ".join(f"{k}x{len(v[0])}({'/'.join(sorted(x.replace('_dispatch','').replace('no_incident','pre') for x in v[1]))})" for k,v in d.items()) or "none"
        tok=0
        for k in("responder_tokens","reflection_tokens"):
            t=inc.get(k); 
            if isinstance(t,dict): tok+=t.get("total_tokens",0)
        per=", ".join(f"{k.split(':')[1]} {v:.0f}" for k,v in c["resolved_s"].items() if v is not None)
        red=",".join(k.split(':')[1] for k,v in c["ever_red"].items() if not v) or "-"
        m=inc.get("injection_to_mitigation_seconds")
        rows.append(f"| {name} | {lab(c['problem_id'],idx)} | {c['faults_resolved']}/{c['faults_total']} | {inc.get('oracle') and ('True' if 'success\': True' in str(inc['oracle'])[:60] else 'False')} | {c['all_resolved_s'] and round(c['all_resolved_s'])} | {m and round(m)} | {tok/1e6:.2f}M | {per} | {red} | {learned} | {inc.get('error') or ''} |")
print("| seq | composite | solved | oracle | last-fault s | inj->mit s | tokens | per-fault s | never red | learned detectors fired after injection start (xN fingerprints, relation) | note |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
print("\n".join(rows))
print()
for name in sys.argv[1:]:
    print(name, "detectors in memory:", detectors(R/name/"application_workspace"))
