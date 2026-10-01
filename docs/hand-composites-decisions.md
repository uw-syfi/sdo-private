# Hand-registered composite faults: decisions and results

Status: 2026-09-30/10-01, n=1 per problem, memoryless Codex gpt-6-luna (medium reasoning) agent, Codex gpt-6-luna judge (judge time excluded from TTD/TTM). Cluster `comp-w40` (1 control plane + 1 worker, deleted afterwards).

## What was built

- `ComposedFailures` (`third_party/sregym/sregym/conductor/problems/composed_failures.py`, submodule branch `vic/exp/hand-composites`, commit `b5160770`). Same-application composition: one shared `HotelReservation` app, so `app.name == "Hotel Reservation"` and the source-deploy gate accepts it.
  - Mitigation: `CompoundedOracle` over each sub-fault's own oracle (success = AND; per-fault results in `oracles`).
  - Diagnosis: `PerFaultDiagnosisOracle`, one judge call per fault against that fault's root cause. The expected text for each fault tells the judge the other faults exist and must not be penalized if named. `success` needs every fault; `accuracy` is the fraction found; `per_fault` gives each fault's verdict and composite score.
  - Validation: 2 to 3 faults, Hotel Reservation only, at most one fault per Deployment (including multi-target ConfigMap faults).
  - Injects in order, recovers in reverse.
- Three registered composites (ids carry no `__v_`):
  1. `composite_readiness_geo__network_policy_recommendation`
  2. `composite_configmap_mongodb_rate__wrong_selector_frontend`
  3. `composite_network_policy_recommendation__configmap_mongodb_geo` (my choice: the hard netpol class paired with the Mongo-adjacent ConfigMap fault the baseline tends to misread).
- 14 SREGym unit tests (`tests/conductor/test_composed_failures.py`): registration, shared app, duplicate-Deployment rejection, AND logic, per-fault credit.

## No-LLM verification (cluster `comp-w40`, fastloop worker)

Every composite deployed from source, both faults injected, both sub-oracles red 25 s after injection, reference recovery (4-5 s) turned both green and the app healthy. Raw: `/mnt/data/shli/comp-runs/verify-composites.jsonl`.

## Baseline results

Run dir: `third_party/sregym/logs/20260930_232252_codex` (extractor `/mnt/data/shli/comp-runs/extract.py`). Stored baseline results under `analysis_full` were NOT reused (a read of that CSV was blocked by the permission classifier); the singles were rerun fresh on the same cluster and code instead.

| Problem | Solved (diag+mit) | Diagnosis | Mitigation oracles | TTD s | TTM s | Tokens (in+out) |
|---|---|---|---|---|---|---|
| composite readiness(geo)+netpol(recommendation) | no | 1/2 faults (geo yes; recommendation 0.11) | readiness green, netpol red | 35 | 82 | 296k |
| composite configmap(mongodb-rate)+selector(frontend) | no | 1/2 (mongodb-rate yes; frontend 0.07) | configmap green, selector red | 62 | 160 | 507k |
| composite netpol(recommendation)+configmap(mongodb-geo) | no | 1/2 (mongodb-geo yes; recommendation 0.34) | both green | 49 | 170 | 936k |
| single readiness(geo) | yes | 0.89 | n/a | 32 | 87 | 315k |
| single netpol(recommendation) | no | 0.11 | red | 50 | 93 | 746k |
| single configmap(mongodb-rate) | yes | 0.74 | green | 51 | 177 | 570k |
| single selector(frontend) | no | 0.00 | red | 44 | 80 | 541k |
| single configmap(mongodb-geo) | yes | 1.00 | green | 35 | 84 | 277k |

Composite solved 0/3; in every composite the model found exactly one fault (the easy one) and missed the other. The missed fault is, each time, a fault that the same model also fails as a single (netpol x2, selector).

## Takeaways

- Meaning: composites are not harder than their hardest component in this data. The solve failures come entirely from the network-policy and wrong-selector classes, which already fail alone. Each composite's easy half was diagnosed and fixed with scores comparable to its single (geo 1.0, mongodb-rate 0.85, mongodb-geo 1.0). TTM of the composites (82-170 s) sits inside the single range (80-177 s). Tokens are higher only for composite 3 (936k vs 746k netpol single).
- Per-fault credit is the useful new signal: 0/3 solved but 3/6 faults found, and it attributes every miss.
- One oddity: composite 3 passed the mitigation oracle (netpol and ConfigMap both green) while diagnosis missed the netpol; the netpol single failed mitigation. Likely the Mongo-side repair incidentally touched the isolated path or restarted pods; n=1 cannot say.
- Confidence: low. n=1 per problem, one 1+1 cluster; judge scores near the 0.70 threshold are noisy.
- Implication: hand composites built from the existing pool add little baseline headroom beyond what netpol and selector already give. Whether a second fault is missed when both faults are individually easy is untested.
- Next step (not done, the push of it was blocked): add an easy+easy composite, `composite_readiness_geo__configmap_mongodb_rate` (singles already measured here), at n>=2. If the baseline still finds both, composites are not a useful difficulty lever for the baseline; if it stops after one fault, the SDO canary below is justified.

## SDO canary: skipped

The baseline is not clearly harder than its components (see Meaning), so the 2-incident SDO canary was skipped. Questions still open for it: whether the health detector opens one incident or two, whether a learned detector for one component fires, and whether a component playbook is used.

## Caveats

- n=1 per composite and per single; a single sample of a stochastic agent.
- The shared app uses the first sub-problem's `HotelReservation` object, so a netpol sub-fault's special wrk2 payload script is only applied when that fault comes first; the source-deploy agent flow does not run the workload.
- Judge expected text mentions the other faults; this is a deliberate design choice and not independently validated against a no-hint judge.
