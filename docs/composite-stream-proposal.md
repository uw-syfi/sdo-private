# Composite-fault stream proposal

Status: design only (read-only scouting, 2026-09-30). No cluster was created and no experiment was run.
Goal: a harder incident stream for Codex gpt-6-luna, built by composing the faults we already run, so that
(a) the memoryless baseline has headroom in solve rate and TTM, and (b) memory can show a learning-curve benefit.

## 1. What SREGym offers today

- **Compound problems exist but are disabled.** `MultipleIndependentFailures(problems=[...])`
  (`third_party/sregym/sregym/conductor/problems/multiple_failures.py`) composes single-fault problems. Every registry entry that
  uses it is commented out (`port_misconfig_*`, `hotel_res_concurrent_fault`, `social_net_concurrent_fault`). The only registered
  "correlated" problems (`faulty_image_correlated`, `update_incompatible_correlated`) are single-mechanism image problems, not
  multi-fault compositions.
- **Grading of a composite.**
  - Mitigation: `CompoundedOracle` runs every sub-problem's mitigation oracle. `success` is the AND of all of them; `accuracy` is the
    importance-weighted mean (so partial repair yields partial `accuracy`, but the pass/fail bit is all-or-nothing).
  - Diagnosis: one `LLMAsAJudgeOracle` against a concatenated narrative ("Fault 1 (Class): ... Fault 2 ..."). The judge is the
    checklist `DiagnosisJudge` (`rca_checklists.yaml` v3.0): 9 yes/no questions in D1 localization, D2 characterization, D3 scope
    precision (weights .33/.33/.34), verdict True iff composite score >= 0.70. A diagnosis that nails only one of two faults
    typically scores roughly 0.45-0.65 (D1-Q1, D2-Q1/Q2, D3-Q2 fail; the "avoid blaming" questions may still pass), so it fails,
    but the composite score is a usable continuous partial-credit signal if we log it. The concatenated narrative is not split per
    fault, so the judge cannot say which half was missed. A per-fault judge call (see section 4) is cleaner.
- **Two blockers for using it as-is on our harness.**
  1. `MultipleIndependentFailures` wraps the sub-apps in `CompositeApp`, whose `name` is `"CompositeApp"`. The conductor's
     source-deploy gate (`unsupported_reason(self.app.name)`) only accepts `"Hotel Reservation"` and `"Social Network"`, so every
     composite would be skipped as "unsupported for source deploy". Same-app composites also lose `frontend_port`, `payload_script`
     and other attributes some oracles read from `problem.app`.
  2. Each sub-problem constructs its own `HotelReservation()` and calls `app.create_workload()`; for a same-app composite this must
     happen once.
- **Fault injectors compose naturally on one namespace** because each mutates a different Kubernetes object: readiness probe (Deployment
  probe), missing ConfigMap (delete `mongo-<x>-script` and restart the Deployment), wrong service selector (Service), NetworkPolicy
  (new object `deny-all-<svc>`), wrong DNS policy (Deployment `dnsPolicy`), misconfig_app (Deployment image). Recovery is per-fault
  and independent. Conflicts arise only when two faults mutate the same Deployment (readiness + wrong DNS + image on one service).
- **Parameter variants per family (Hotel Reservation).**

| Family | Base problem | Variants available | Constraint |
|---|---|---|---|
| readiness probe | `readiness_probe_misconfiguration_hotel_reservation` (frontend) | `__v_hotel_reservation_{geo,search,user,profile,recommendation,reservation}` | none |
| missing ConfigMap | `missing_configmap_hotel_reservation` (mongodb-geo) | `_mongodb_rate_`, `_mongodb_geo_rate_` (only geo and rate have a required ConfigMap) | 3 targets total |
| wrong service selector | `wrong_service_selector_hotel_reservation` (frontend) | generated variants exist but can never pass (oracle probes frontend port) | frontend only |
| network policy block | `network_policy_block` (recommendation) | class takes `faulty_service`, no registry entries | needs registry entries |
| wrong DNS policy | `wrong_dns_policy_hotel_reservation` (profile) | `wrong_dns_policy__v_hotel_reservation_<svc>` generated (unverified on source deploy) | verify |
| misconfig app | `misconfig_app_hotel_res` (geo) | none | geo only |

## 2. Evidence that the current faults are too easy (and where they are not)

Sources: `/mnt/data/shli/stream-runs/analysis_full/incidents_all.csv` (24-incident stream, memoryless Codex arm and SDO arm),
`/mnt/data/shli/lfint-runs/table_*.json`, `/mnt/data/shli/detgen-runs` (evidence dirs, logs), and the decision logs in
`benchmarks/sregym/experiments/*DECISIONS.md` (worktree `detgen`). `passed` = both diagnosis and mitigation graded true.
TTM is judge-free, over passed incidents only. Tokens are raw per-incident means.

| Fault family (targets) | Baseline n | Baseline solved | Baseline TTD (mean) | Baseline TTM | Baseline tokens | SDO solved | SDO TTM |
|---|---|---|---|---|---|---|---|
| network-policy-block (recommendation) | 4 | **0/4** | 78 s (highest) | - | **730k** (highest) | 4/4 | 49 s |
| wrong-service-selector (frontend) | 2 (+2 in the 8-stage luna sequence) | 1/2 (0/2 in the sequence) | 56 s (sequence: 61-111 s) | 58 s | 507k | 1/1 measurable (1 "never manifested") | 118 s |
| missing-configmap (geo / rate / geo+rate) | 5 (+2 in sequence) | 5/5 (sequence 1/2) | 42 s | 89 s | 338k | 5/5 | 51 s |
| wrong-dns-policy (profile) | 1 | 1/1 | 43 s | 62 s | 219k | 0/1 (graded False, TTM 404 s) | - |
| misconfig-app (geo) | 1 | 1/1 | 47 s | 62 s | 426k | 1/1 | 94 s |
| readiness-probe (frontend + 6 variants) | 11 | 10/11 | 35 s | 66 s | 279k | 10/10 | 103 s |

Aggregate on the 24-incident stream: baseline 18/24 solved (the `summary.json` 22-incident view shows 16/22), TTD mean 38 s,
passed-TTM mean 71 s; detector-generalization (9-incident readiness/ConfigMap stream) and the late-finding pull-validation
(3 to 4 readiness/ConfigMap incidents per arm, TTM 47-96 s except one 262 s outlier) were solved throughout.

Takeaways from the data:

1. **The premise holds for the readiness and ConfigMap families**: readiness is 10/11 with TTD about 35 s and TTM about 66 s;
   ConfigMap is 5/5 in the stream. These are the families the detector-generalization and pull-validation experiments used, which
   is why those experiments saw no solve-rate or TTM headroom.
2. **It does not hold for network-policy-block and wrong-service-selector.** The baseline solved 0/4 network-policy incidents in the
   stream and 1/2 in the earlier 8-stage sequence run; in every failed run it blamed the MongoDB backends (a recurring red herring:
   the app ships fault-script ConfigMaps that Codex reads as injected state) and the judge scored localization 0.00. Those two
   families also have the largest baseline TTD (78 s, 56-111 s) and tokens (730k, 507k). Yet SDO solved network-policy 4/4 with TTM
   of 49 s, and the repeats were fast (24-39 s). So this is exactly where memory helps, and it is the only place in current data with a measurable baseline
   gap. The stream-level story is therefore: the easy families dilute the hard ones.
3. **Stalls and graded failures come from harness hazards, not difficulty**: the wrong-service-selector "never manifested" incident
   (source fix persisted), the `wrong_dns_policy` SDO failure (judge localization 0.00 while the live fix worked), the A4 manifest
   corruption (responder committed a live-patched manifest with a duplicated `imagePullPolicy`), and the coreDNS-scoped
   `service_dns_resolution_failure` (outside the application namespace). These are risks for composites too (section 5).

## 3. Proposed composite stream (11 incidents)

Design rules:
- Component faults come from the verified-usable pool only: readiness probe (A), missing ConfigMap (B), network policy block (C),
  wrong service selector (D, frontend only), wrong DNS policy (E, profile base only; other targets need a verification pass).
- No two components mutate the same Deployment; no component's target is a dependency or dependent of the other's target
  (Hotel Reservation call graph: frontend -> search -> {geo, rate}; frontend -> {recommendation, user, reservation, profile};
  geo/rate -> mongodb-geo/mongodb-rate). Hence readiness targets that pair with ConfigMap faults are frontend, user, profile,
  reservation; not geo, search (downstream of geo/rate databases) and not recommendation (netpol target).
- Symptoms are distinct but interact: a frontend-facing error rate is shared, but each fault has its own unhealthy object
  (NotReady endpoints, pods stuck ContainerCreating on the missing mount, isolated pod, endpoint-less Service).
- Interleave families as A1 A2 B1 A3 B2: each composite "family" is a pair of component classes; parameters vary between members.

| # | Composite | Components (class, target) | Kind | Reuse (what a learned detector/playbook can transfer) |
|---|---|---|---|---|
| 1 | F1a | A readiness(frontend) + B configmap(mongodb-geo) | first | none (cold) |
| 2 | F2a | C netpol(recommendation) + A readiness(user) | first-of-family | A class detector, new C |
| 3 | F1b | A readiness(profile) + B configmap(mongodb-rate) | variant of #1 | both components, both with new targets |
| 4 | F3a | D selector(frontend) + B configmap(mongodb-geo) | first-of-family | B exact target from #1, new D |
| 5 | F2b | C netpol(recommendation) + A readiness(reservation) | variant of #2 | C exact, A new target |
| 6 | F1c | A readiness(user) + B configmap(mongodb-geo+rate) | variant of #1/#3 | A target seen in #2, B both targets |
| 7 | F4a | E dns-policy(profile) + C netpol(recommendation) | novel pair | C exact from #2/#5, E new |
| 8 | F3b | D selector(frontend) + A readiness(reservation) | variant of #4 | D exact, A target seen in #5 |
| 9 | T1 (triple) | A readiness(user) + B configmap(mongodb-rate) + C netpol(recommendation) | composite of learned classes | A,B,C all seen, never co-occurring |
| 10 | F2c | C netpol(recommendation) + B configmap(mongodb-geo) | variant of #2 | C exact, B exact from #1/#4 |
| 11 | F1a exact repeat | same as #1 | exact | full composite (upper bound on memory benefit) |

Why this should expose memory benefit: the baseline starts every incident from scratch and, by the evidence above, is most likely to fail
on C and D and to spend the most tokens there, and two simultaneous faults add a second localization the judge must see
(D1-Q2/D3-Q2 fail if either is missed). SDO's learned detectors for A, B, C and its playbooks for each class can fire separately on a
composite (#9 is the sharp test: three known classes with a never-seen combination, and a flat 0/1 baseline
expectation on C). Expectation to state up front: memory should help on #3-#11 first through per-component transfer, and the
triple and exact repeat are the contrast points. Hypotheses about the magnitude are not validated.

Decoy and overlap risks, with mitigation:

| Risk | Example | Avoidance |
|---|---|---|
| Two faults on one Deployment (second overwrites or masks the first) | readiness(geo) + misconfig_app(geo); dns-policy(user) + readiness(user) | Validator in the composite generator rejects a repeated target Deployment |
| Dependency overlap makes one fault the cause of the other's symptoms | readiness(geo) + configmap(mongodb-geo); readiness(search) + configmap(rate/geo) | Targets pair only across disjoint subtrees (table above) |
| Frontend double-hit hides the second fault | selector(frontend) + readiness(frontend) | D is frontend only, so D never pairs with A(frontend); A(frontend) only appears in #1 |
| Known red herring: app ships Mongo fault-script ConfigMaps, Codex reads them as injected state | C or D composed with a MongoDB-adjacent fault (B) | Keep it (it is the point of difficulty for the baseline) but verify the judge accepts a diagnosis naming both; report per-fault localization |
| Mitigation oracle side effects: generic `MitigationOracle` requires all pods Running | `restore-mongo-roles-*` left in phase Succeeded fails the oracle | Already known; unchanged, but composites double its exposure |
| Persistent-workspace source repairs commit live manifests (A4 incident: duplicated key broke the next deploy) | any composite repairing two Deployments | Add a manifest `kubectl apply --dry-run` guard before stage handoff (branch `manifest-guard` exists) |
| Unverified injectability | wrong_dns_policy variants other than profile; netpol variants on other services | Run the no-LLM verify-faults harness (`stream-runs/verify-faults*.jsonl` method) on every new composite before the paid run |
| Destructive or out-of-namespace faults | sidecar/service port conflict, duplicate PVC, coredns-based DNS failure | Excluded from the pool (see `STREAM_LEARNING_CURVE_DECISIONS.md`) |
| Incident grouping: the health detector may open one incident for both faults, or two; reflection must learn both | all | Open question for the controller side; measure in a 2-incident canary before the full run |

## 4. Required code

1. **SREGym submodule (branch, then bump pointer; per repo policy only under `third_party/sregym` and mirrored by a pin commit).**
   - `ComposedFailures(Problem)` (new, `conductor/problems/multiple_failures.py` or sibling): same-app composition. `app` is the
     single shared `HotelReservation` (so `app.name == "Hotel Reservation"`, source deploy works, frontend_port/payload script retained),
     sub-problems reuse its namespace, `create_workload()` runs once. `mitigation_oracle = CompoundedOracle(...)` as today;
     `diagnosis_oracle` = a thin wrapper that judges the diagnosis against each sub-fault's `root_cause` separately (one judge
     call per fault, each with the usual 3-round vote), returns `success = all(...)` and reports `per_fault` scores and the fraction
     solved. Alternative: keep the single concatenated judge (cheaper, no code) and log only composite score; I choose the
     per-fault judge because it gives partial credit and attributes misses.
   - Validation in `__post_init__`/constructor: reject duplicate target Deployments, reject >3 components, reject unsupported app mix.
   - Registry entries: `composite_<pair>` ids for the 11 incidents (e.g. `composite_readiness_frontend__configmap_mongodb_geo`),
     plus `network_policy_block__v_hotel_reservation_<svc>` entries if a non-recommendation netpol target is ever wanted.
     Unnecessary for this stream: all C uses recommendation.
   - Stream-id convention `__c_` for composite ids, excluded from `get_problem_ids` the same way `__v_` is.
   - Injection order/timing: inject sequentially with a short delay (already `time.sleep(1)` per fault) and recover in reverse order.
2. **`benchmarks/sregym/runner/incident_stream.py`** (currently only in branches `stream-lc`/`detgen`/`lfint`/`pullfind`, not on `main`;
   this work must first land that module or branch from it).
   - A `Catalog` of composite families: `FaultFamily` generalizes to a *composite family* whose `problem_id`/`variants` are composite ids
     and whose `components` (class + target) are recorded for reuse labeling.
   - New incident kinds beyond `first/novel/exact/variant`: `partial` (shares >= 1 component class with an earlier incident; the
     "transfer" kind) and `recombined` (all component classes seen, never together, like #9). `_is_learnable` needs an analogue.
   - A static, hand-written order is acceptable for 11 incidents (like `STREAM_OPENING`); a seeded generator is only needed for 200.
   - Renders pipeline + baseline TOMLs and manifest as now; adds component columns to the manifest so the analysis can split
     per-component learning.
3. **Adapter/judge handling.** The benchmark adapter already passes the diagnosis text through; it needs (a) `per_fault` scores
   from the new oracle written into the results CSV, (b) `stream_curve` to count a stage solved only when both the diagnosis
   and mitigation are true and also to plot fraction-of-faults-solved, (c) `allow_failed_verdicts=true` (exists). The strict-receipt
   check is per incident and should be unaffected; confirm that a composite produces one receipt (grouping question above).
4. **Analysis references.** Update `.agents/skills/analyze-experiment/references/` (new result columns) and the paper-scope matrix if
   composite incidents enter the paper claims.
5. **Tests first** (repo convention): `ComposedFailures` rejects a repeated Deployment; app name is preserved; diagnosis wrapper
   returns `success=False` and `per_fault=[True, False]` for a one-fault diagnosis (stubbed judge); stream generator places a
   `recombined` incident only after all its component classes; render round trip.

## 5. Baseline re-run, cost and time

The incident set changes, so the prior baseline arm (18/24 on the single-fault stream) is not comparable. A fresh memoryless Codex
(gpt-6-luna, medium) baseline over the same 11 composite incidents is required; it can also be repeated x2 because the baseline solve rate on C/D
is noisy (sequence run: 1/4 then 3/4 on repeat problems). Keep `verify_protocol` off (raw baseline) and optionally add the existing
`codex_luna_verify_baseline` arm as a second control.

Estimates (from the 24-incident stream; composite numbers are extrapolations, not measurements):

| Quantity | Single fault (measured) | Composite, 2 faults (estimate) | Triple (estimate) |
|---|---|---|---|
| Baseline TTD | 35-78 s | 50-120 s | 70-150 s |
| Baseline judge-free TTM | 45-156 s | 90-250 s | 150-350 s |
| Baseline raw tokens | 0.2-0.8M | 0.5-1.5M | 0.8-2.0M |
| Baseline wall clock per incident (redeploy + fault + agent + 3-round judge) | about 3-4 min | about 5-8 min | about 8-12 min |
| SDO raw tokens (responder + reflection) | 0.2-2.7M | 1-4M (two reflections' worth of learning on cold composites) | 2-5M |
| SDO wall clock per incident (incl. reflection drain) | about 4-10 min | about 8-15 min | about 12-20 min |

Totals: baseline arm about 1 hour on one 1+1 kind cluster per pass (two passes about 2 hours, parallelizable to 1 hour); SDO arm about
1.5-2.5 hours on its own cluster, with a one-time cold lifecycle of about 15 min and about 1M tokens unless the existing lifecycle seed
(`/mnt/data/shli/detgen-runs/seeds/lifecycle-stream`) is reused. Prior to any paid run: a no-LLM verify pass of all 11 composite ids
(inject, oracle false, recover, oracle true), about 1 hour with no Codex usage. Judge cost rises because the judge runs per fault
(2-3 calls of 3 rounds each at xhigh), and judge time stays outside TTD/TTM as before.

## 6. Takeaways

- **Meaning.** The "too easy" finding is true for the readiness and missing-ConfigMap families that the last two experiments
  used (baseline 15/16, TTM about 66-89 s), but not for the whole catalog: the memoryless baseline fails network-policy-block
  0/4 and wrong-service-selector 1/4 across the two recorded runs, and spends the most tokens there. Headroom exists in
  the two families the previous streams diluted with easy incidents. Composing faults should add a second localization and a
  stricter all-or-nothing gate on top of that.
- **Confidence.** Medium on the evidence table (small n per family: 1-11 incidents, single seeds, one stochastic baseline);
  low on the composite time, token and solve-rate estimates (extrapolated, nothing composite has been run); medium-low that SDO's
  learned detectors fire independently on two simultaneous faults (single-fault firing is evidenced, composite grouping is untested).
- **Implication.** Do not spend more paid runs on readiness/ConfigMap-only streams. A composite stream built around C and D plus
  A and B gives the baseline a real failure mode and gives memory a per-component transfer path (#3-#11). The generic
  `MultipleIndependentFailures` cannot be used directly (source-deploy gate rejects `CompositeApp`, single concatenated judge),
  so the submodule needs a small same-app composition class and a per-fault diagnosis judge.
- **Next step.** (1) Land `incident_stream.py` on `main` (it exists only in experiment branches) and write the failing tests from
  section 4. (2) Implement `ComposedFailures` plus the per-fault judge in a submodule branch. (3) Run the no-LLM verify pass
  on all 11 composite ids and a 2-incident SDO canary (#1, #3) to settle incident grouping. (4) Launch the fresh baseline (x2) and the SDO arm
  in parallel on separate 1+1 kind clusters. (5) Report per-component learning, not only stream-level solve rate.
