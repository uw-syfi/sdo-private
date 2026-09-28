# Small-scale assurance program: plan

Owner: autonomous agent (branch `vic/exp/assurance-plan`), started 2026-09-28.
Status: **planned, not run.** No live LLM run may start before the Codex
weekly window resets on 2026-10-03 18:19 UTC. New starts stop hard at 97% of
the window.

## Purpose

We have reimplemented nearly the whole prototype. This program checks, at small
scale, the key results the paper reports, and it checks that SDO is reliable and
efficient in tokens and wall clock against a raw Codex baseline. Every claim
below has a pre-registered pass criterion, a sample size and a confidence-interval
method, written before any run.

## Fixed rules (user-directed)

- **Models: gpt-6-luna only (user rule, 2026-09-28).**
  - Every SDO agent role runs Codex `gpt-6-luna`: responder, reflection, lifecycle deployer and health judge.
  - The SREGym judge is `codex-gpt-6-luna` at `xhigh`. That is the Codex CLI backend's default `JUDGE_REASONING_EFFORT`, and no config may override it.
  - The Codex baseline arms also run `gpt-6-luna`.
  - There are no Claude, Haiku, Gemini or gpt-5.x arms. The existing `sdo_claude_haiku_*`, gpt-5.4 and gemini configs are legacy and are never used here.
  - `tests/unit/benchmarks/sregym/runner/test_assurance_configs.py` enforces this for every assurance and luna config.
- **Effort:** agents run at `medium` (`reasoning_effort = "medium"` on every arm; SDO pins `INCIDENT_REASONING_EFFORT`).
- **Three arms:**
  - **SDO** (`sdo_codex`, persistent controller);
  - **Codex stock** (`codex`);
  - **Codex + verify** (`codex` with `[agent.codex] verify_protocol = true`).
- **Topology:** 1 control plane + 1 worker kind lanes (`kind_worker_nodes = 1`), `worker_cpu_limit = "3"`.
- **Timing: judge-free TTD and TTM only.** These are defined in `luna_reuse_DECISIONS.md` ("User-directed: judge time is excluded from TTD and TTM").
  - TTD is the diagnosis POST minus the injection.
  - TTM is `incident_cost`'s judge-free headline: the last state-changing mutation after the injection.
  - Raw times that include the judge are supplementary only.
- **Reports:** every report has a **Takeaways** block: what the data shows, confidence, implication for SDO, and next action.
- **Disk:** keep at least 100 GB free on `/mnt/data`. Budget about 20 GB per 1+1 lane.
- **Clusters:** never touch `luna-*`, `sdo-reuse-w0`, `sregym-live-shared`, `sregym-w0` or `sregym-w1`. Assurance lanes are named `assure-w0` … `assure-w7`. No-LLM smokes use `assure-p*` and are deleted afterwards.

## (a) Claim map

The paper's evaluation (`sdo_paper/secs/eval.tex`, abstract and intro) makes the claims below. The paper ran Claude Sonnet 4.6 over a 200-incident composed stream on four apps. We keep its questions but shrink the scale, and we compare against the directed baselines rather than the paper's History-Embed and History-LLM.

**Statistics used throughout.**
- **Time and token ratios:** ratio of medians, with a stratified bootstrap. Resampling is within each (problem, arm) cell, 10,000 replicates, seed 20261003, percentile 95% CI. A pooled ratio pools the per-cell resamples, so each problem keeps its weight.
- **Proportions:** Wilson score 95% CI.
- **Differences of proportions:** Newcombe hybrid-score 95% CI (method 10).
- **Tokens:** reported raw and weighted. The weights are `cache_read = 0.1`, `output = 8`, in base-input units (see `luna_reuse_DECISIONS.md` 3.3).
- **Verdicts:**
  - A criterion **passes** when its point estimate and its CI bound both meet the threshold.
  - It is **directional** when the point estimate meets the threshold but the CI bound does not.
  - Otherwise it **fails**.
  - Directional results are reported as such and never rounded up to a pass.
- **Phases:** phase 1 (P1) and phase 2 (P2).
- **n** below counts the phase-1 cells unless marked P2.

| # | Paper claim (where, effect size) | Small-scale experiment | Metric | Pre-registered pass criterion | n, CI |
|---|---|---|---|---|---|
| C1 | Recurring faults resolve faster: median TTR 282 s vs 584 s, **2.1×** (§5.3 overlap); abstract and intro "cuts MTTR by 52% on recurring faults" | Each SDO pipeline runs every phase-1 problem twice (round 1, then round 2). The round-2 exact repeat is compared with memoryless Codex attempts on the same problem | TTM ratio Codex-median / SDO-warm-median, pooled over problems, and per problem | Pooled ratio **≥ 2.0**, bootstrap lower bound **≥ 1.5**. Per-problem point ratio ≥ 1.5 on ≥ 4 of 5 problems. The same test runs against Codex + verify | SDO warm 4 per problem (20); each Codex arm 5 per problem (25); stratified bootstrap |
| C2 | Recurring faults use fewer tokens: median 2.2M vs 5.4M, **2.5×** fewer (§5.3) | Same cells as C1 | Weighted incident tokens, SDO-warm / Codex-stock (ratio of medians). A warm incident includes any reflection it triggers | **Direction:** ratio ≤ 0.8 with bootstrap upper bound < 1.0. **Paper magnitude:** ratio ≤ 0.4. Step 3 measured 0.66 on one problem, so we expect direction to pass and magnitude to fail | Same as C1 |
| C3 | Novel faults are not degraded: accuracy and TTR similar to memoryless, tokens **+17.7%** at the median (§5.3 no-overlap) | Round-1 first encounters, including those after other faults are already in memory (the "non-empty memory, no overlap" case) | TTM ratio SDO-cold / Codex-stock. End-to-end success difference. Responder-only tokens ratio. Total cold tokens including reflection are reported, not gated | **Time:** ratio ≤ 1.25, upper bound ≤ 1.6. **Accuracy:** SDO − Codex e2e ≥ −0.10 on the point estimate, Newcombe lower bound > −0.30. **Tokens:** responder-only ratio ≤ 1.25 reproduces the paper. Step 3 measured about 1.9×, so a token fail is expected and is reported as a known gap | SDO cold 4 per problem (20) vs 25 |
| C4 | SDO solve rate ≥ memoryless: e2e 65.4% vs 62.7% (Table 3). Fixes are double-checked (§3) | All stages of all arms | End-to-end success (diagnosis judge pass AND mitigation oracle pass). **False closure:** SDO's controller closes an incident while the mitigation oracle fails | **SDO e2e ≥ 0.90** with Wilson lower bound ≥ 0.75 over all SDO stages. SDO − Codex-stock ≥ 0 on the point estimate (Newcombe CI reported). SDO ≥ Codex + verify − 0.05. **Zero** SDO false closures (hard) | SDO 40 stages; each Codex arm 25 attempts |
| C5 | Learning curve: rolling TTR and tokens fall as detectors and playbooks accumulate, while memoryless stays flat. Detectors stay fewer than playbooks (§5.1, Fig. 4) | SDO round 1 → round 2 over one pipeline. Codex split by stream position | Per-problem round-2/round-1 TTM ratio, pooled. Memory growth from `.sdo/` after each stage | SDO pooled ratio **≤ 0.5**, upper bound ≤ 0.7. The Codex first-half/second-half ratio CI contains 1. After round 1, every lane has ≥ 1 playbook and ≥ 1 incident detector per distinct root-cause class (4 in phase 1), and the detector count is ≤ the playbook count | 4 pipelines × 5 problem pairs |
| C6 | Detectors retrieve cheaply and accurately: precision 0.48, recall 0.73, FP 0.30, **10.8 s and 0 tokens** vs Playbook-LLM's 483 s and 148k (§5.4, Table 5) | **Fastloop, no LLM (no quota).** Freeze each lane's post-phase-1 memory. Inject each phase-1 problem (overlap set) and each phase-2-only problem (no-overlap set) with no responder dispatched, plus 30 min healthy. Score the surfaced playbooks against catalog labels | Pooled precision and recall on the overlap set. FP rate (any playbook surfaced) on the no-overlap set and on healthy windows. Median detection latency. LLM tokens spent on retrieval | Recall **≥ 0.73**, precision **≥ 0.48**, FP **≤ 0.30**, median latency **≤ 20 s**, retrieval tokens **= 0** | 4 lanes × (5 overlap + 3 no-overlap) injections; Wilson CIs on pooled counts |
| C7 | Memory transfers to recurrences with different parameter bindings (§5.3 overlap set; `TODO.md` "parameterized patterns generalize") | **P2:** variant V1 (`wrong_service_selector` on `search` instead of `frontend`) runs in round 2 after the frontend fault is in memory. **P1 (partial):** composite K1 contains the `mongodb-rate` variant of S1 | TTM ratio Codex / SDO on the variant. Whether the family detector fires | Ratio **≥ 1.5** reproduces the paper, and the family detector fires in ≥ 3 of 4 pipelines. Step 3 found that a family-level match runs at cold speed, so a directional result is the expectation | P2: 4 SDO vs 5 per Codex arm |
| C8 | Composed incidents: several detectors fire and are batched into one session. Agents sometimes mitigate only a subset of the faults (§3.4, §5.1, §5.3) | Composites K1 and K2 in phase 1, plus K3 in phase 2 (catalog in (c)). **Fastloop, no LLM:** a scripted partial fix must keep the closure gate unhealthy | Full-mitigation rate (every component's oracle passes). Partial-fix rate. SDO false closures on composites. Gate verdict after a scripted partial fix | SDO full-mitigation **≥ 0.75** (Wilson reported) and ≥ Codex stock on the point estimate. SDO false closures **= 0**. The scripted partial fix keeps `sdo incident status` unhealthy in **3/3** trials per composite, and the full fix clears it in 3/3 | SDO 16 composite stages (P1) vs 10 per Codex arm; gate 3 trials per composite |
| C9 | "Token cost scales with the number of incidents rather than with operating time" (abstract, §3.3) | **Fastloop, no LLM.** A controller holding learned memory runs 60 min on a healthy app with the decoys present | LLM tokens, responder dispatches, detector findings | **0 tokens, 0 dispatches** in each of 3 lanes | 3 × 60 min |
| C10 | Deployment from source is reliable with the independent health judge: 11/11 vs 4/11 single-agent. The architecture summary cuts time and tokens by 47.5% and iterations by 2.5× (§5.2) | **P2 only:** 3 fresh hotel-reservation lifecycles, validated by the external load generator. The judge and summary ablations are deferred (paper-scale cost, and not on the incident path) | Lifecycle success. Deploy time, tokens, iterations | **3/3 succeed.** Time and tokens are reported against the Step 3 lifecycle (about 13 min, 2.09M raw) | 3 lifecycles; Wilson |
| C11 | Memory is safe: SDO rejects misleading playbooks and decoys and is not misled (§5.3 "inspects and discards false positives"). New for this program: SDO vs a verify-only baseline | Decoy exposure in every hotel stage (`failure-admin-*`). Composite K3 (a benign drift beside a real fault). Codex + verify isolates the value of the verify loop | Decoy-driven failures (a trace shows a decoy acted on as the cause). SDO's warm advantage over Codex + verify | SDO decoy-driven failures **= 0**. The C1 ratio against Codex + verify is **≥ 2.0**, which shows the speedup is memory and not only verification | All stages |

**Deferred, with reasons.**
- **History-Embed and History-LLM baselines, and the Playbook-Embed and Playbook-LLM retrieval comparison (§5.1, §5.4).** Not in the directed arms, and not reimplemented in the new prototype. C6 measures the detector side in absolute terms against the paper's numbers.
- **Knowledge resilience and staleness (`TODO.md` §5.5).** Not in the current paper text, and topology-change problems do not exist yet.
- **Multi-app incidents (socialNetwork, fleetcast, train-ticket).** The paper's stream spans four apps. We keep hotel reservation for phase 1 and 2 because it is the only app with a validated lifecycle seed, traffic workload and decoy analysis. Adding a second app costs one lifecycle (about 0.3%) plus qualification. It is the first extension if phase 2 passes.

## (b) Problem selection

**Criteria.**
1. It is registered in SREGym and bound to `HotelReservation`, so it deploys from source.
2. Where possible, it has already run live in our harness (Step 3 or feedback-loop e2e).
3. Together, the set covers the paper's fault classes: misconfiguration, dependency failure, network, routing and resource.
4. Every one qualifies in the no-LLM fastloop (inject → oracle fails → recover → oracle passes) before any quota is spent.

**Phase 1 (5 problems).**

| ID | SREGym problem | Class | Target | Live history | Why |
|---|---|---|---|---|---|
| S1 | `missing_configmap_hotel_reservation` | dependency / config (ConfigMap deleted) | `mongodb-geo` | Step 3: n=6 SDO, n=5 Codex | Anchors Step 3. Decoy-prone for Codex |
| S2 | `wrong_service_selector_hotel_reservation` | service routing (Service selector) | `frontend` | sequence: Codex failed 2/2 | Healthy pods with no endpoints. Strong decoy pull |
| S3 | `network_policy_block` | network (deny-all NetworkPolicy) | `recommendation` | feedback-loop e2e: SDO 3/3, Codex 1/3, verify 0/3 | Symptoms are intermittent. SDO's static rule matches it on first encounter, so it is an easy cold case for SDO (see its note in `luna_reuse_DECISIONS.md`) |
| K1 | `composite_policy_and_rate_configmap_hotel_reservation` | composite: two independent faults on different services | `recommendation` + `mongodb-rate` | components live | The user's canonical composite. Its rate ConfigMap is an S1 variant |
| K2 | `composite_frontend_selector_and_readiness_hotel_reservation` | composite: two causes, one symptom, one service | `frontend` + `frontend` | components live | A partial fix leaves the symptom |

**Phase 2 (8 problems) = phase 1 + 3.**

| ID | SREGym problem | Class | Target | Qualification | Why |
|---|---|---|---|---|---|
| S4 | `resource_request_too_large` | resource (memory request above node capacity, pod Pending) | `mongodb-rate` | new, needs fastloop qualification. Fallback: `readiness_probe_misconfiguration_hotel_reservation` | Adds the resource-constraint class the paper names |
| V1 | `wrong_service_selector__v_hotel_reservation_search` | variant of S2 (other binding) | `search` | new generated variant. Check that `ServiceEndpointMitigationOracle` uses `search`'s port (it uses `frontend_port` for hotel). Fallback: `missing_configmap_mongodb_geo_rate_hotel_reservation` (live, Codex failed it) | C7 generalization. Runs in round 2 only |
| K3 | `composite_geo_configmap_with_log_drift_hotel_reservation` | composite: fault plus misleading benign drift | `mongodb-geo` + a decoy on `geo` | new decoy injector | "Fault combined with a misleading symptom". The drift appears in the healthy-state diff next to the real cause |

**Excluded, and why.**
- Node-level, Khaos, metastable and wrk-oracle problems (`node_clock_drift`, conntrack, calico, Khaos, RPC storms, `ephemeral_port_range`): they are expensive or unsupported on kind 1+1.
- `revoke_auth_mongodb-*` and `storage_user_unregistered-*`: their app is built with `mount_failure_scripts=False`, which conflicts with the default decoy mounts in a persistent, shared deployment. The fault also *is* what the decoy scripts describe, which makes grading ambiguous.
- `misconfig_app_hotel_res`: it swaps in a prebuilt remote image (`yinfangchen/geo:app3`), which does not fit source deploy.

**Coverage.**
- ConfigMap and dependency: S1, K1, K3.
- Service routing: S2, K2, V1.
- Network: S3, K1.
- Workload spec (probe): K2.
- Resource: S4 (P2).
- Composites: K1 (independent), K2 (same symptom), K3 (misleading).
- Variants: K1's rate ConfigMap (P1), V1 (P2).

## (c) Composite fault catalog

**Semantics.** They are implemented in SREGym as `CompositeFaultProblem`; see "Implementation" below.
- A composite injects its components in catalog order and recovers them in reverse order.
- Every component recovery is attempted even if an earlier one raises. The errors are then raised together.
- **Mitigation passes only when every fault component's own mitigation oracle passes** (`CompoundedOracle`, success is AND).
- Decoy components are not graded for mitigation. Leaving a decoy in place or reverting it are both correct.
- The diagnosis ground truth lists every fault component's root cause and names each decoy as not a cause. The LLM judge grades against that text.

| ID | Components (in injection order) | Intended difficulty | Expected healthy-state diff at dispatch | Correct mitigation | Partial-fix trap |
|---|---|---|---|---|---|
| K1 `composite_policy_and_rate_configmap_hotel_reservation` | `network_policy_block` (deny-all on `recommendation`); `missing_configmap_mongodb_rate_hotel_reservation` (delete `mongo-rate-script`, restart `mongodb-rate`) | **Two simultaneous, independent faults on different services with different symptoms.** Recommendations time out intermittently (network). Search and rate fail hard, because `mongodb-rate` is stuck `ContainerCreating` on the missing volume. The louder rate failure can hide the network fault, which only shows as intermittent timeouts (see the S3 e2e note) | `NetworkPolicy/deny-all-recommendation` added. `ConfigMap/mongo-rate-script` removed. `Deployment/mongodb-rate` template or replicas changed by the injector's scale 0→1 | Delete `deny-all-recommendation`. Recreate `mongo-rate-script` from the application source, then let or make `mongodb-rate` roll out | Fixing only the ConfigMap leaves recommendations blocked, and the traffic detector may be quiet, so only the static isolation rule or the diff reveals it. Fixing only the policy leaves `mongodb-rate` down |
| K2 `composite_frontend_selector_and_readiness_hotel_reservation` | `wrong_service_selector_hotel_reservation` (extra label on `Service/frontend` selector); `readiness_probe_misconfiguration_hotel_reservation` (broken readiness probe on `frontend`) | **Two causes, one symptom, one service.** Both leave `Service/frontend` with no ready endpoints, so every user request fails the same way. **Ordering and masking:** until the selector is fixed, the probe fault cannot be seen through the endpoints, because the Service selects no pods at all | `Service/frontend` selector changed. `Deployment/frontend` readiness probe changed | Restore the selector AND restore the readiness probe (from the source manifests) | Fixing either one alone leaves "frontend has no ready endpoints" unchanged. This is the key closure-gate test: an agent that stops after the first plausible fix fails |
| K3 `composite_geo_configmap_with_log_drift_hotel_reservation` | `missing_configmap_hotel_reservation` (delete `mongo-geo-script` for `mongodb-geo`); decoy `benign_env_drift` (set `LOG_LEVEL=debug` on `Deployment/geo`) | **A fault plus a misleading symptom.** `geo` is the service whose requests fail, and it also shows a fresh, unexplained config change and a burst of new debug logs. The drift is harmless: `tune/setting.go` only changes zerolog's level. The pre-existing `failure-admin-*` decoy scripts are also present | `ConfigMap/mongo-geo-script` removed. `Deployment/mongodb-geo` changed by the injector. `Deployment/geo` env `LOG_LEVEL` added (the decoy) | Recreate `mongo-geo-script` and roll out `mongodb-geo`. Reverting `LOG_LEVEL` is allowed but not required | Reverting only the `geo` env drift, the "obvious recent change", fixes nothing. The diagnosis must not name the drift as the root cause. With evidence-verified diagnosis (D20), citing the drift as a `state-change` gets an `unverified` verdict, because no detector clears |

**Reserve composites** (not scheduled; for replacements or a phase-3 extension):
- **K4** `readiness_probe_misconfiguration_hotel_reservation` + `missing_configmap_hotel_reservation`: two services, and the frontend outage masks the geo failure.
- **K5** a three-fault composite, S2 + S3 + the rate ConfigMap: a breadth stress test.

**Implementation, with the decision logged below.**
- The generic class `CompositeFaultProblem`, the decoy injector and the named composite table live on the SREGym fork branch `vic/feat/composite-problems`, as `sregym/conductor/problems/composite.py` plus the JSON table `composite_specs.json`.
- The registry merges the table in, as it does the generated variants.
- Because both the conductor and the fastloop worker build problems through `ProblemRegistry.get_problem_instance`, **the fastloop gets composites for free.** Its `inject`, `oracle` and `recover` requests take a composite ID like any other problem ID.
- The first-party catalog (`benchmarks/sregym/experiments/assurance/composites.toml`) holds the assurance metadata: difficulty, expected diff, correct mitigation and roles. A test checks that it matches the submodule's JSON table.

## (d) Run matrix, lanes and quota

**Stream design (SDO).**
- One pipeline is one lane with one persistent controller.
- Round 1 runs the phase's problems in a rotated order. Round 2 repeats round 1 in the same order.
- Four pipelines use four cyclic rotations of the order:
  - A: S1 S2 S3 K1 K2
  - B: S2 S3 K1 K2 S1
  - C: S3 K1 K2 S1 S2
  - D: K1 K2 S1 S2 S3
- Every problem therefore appears once at each of four stream positions. Every composite is also seen once before its components (D for K1) and once after them.
- Stage 0 seeds from the lifecycle-only seed of the feedback-loop e2e (`30e023d`, or its successor on main), so no lifecycle quota is spent. Budget one lifecycle per lane as a contingency.

**Codex arms.**
- They are memoryless, so every attempt is an independent sample.
- Each arm runs 5 attempts per problem, spread over 2 lanes in interleaved problem order, so problem and lane are not confounded.

**Phase 1 matrix (8 lanes, about 2 h wall clock).**

| Lanes | Arm | Work | n per problem |
|---|---|---|---|
| `assure-w0`…`w3` | SDO | 4 pipelines (rotations A–D) × 10 stages | 4 cold + 4 warm |
| `assure-w4`, `w5` | Codex stock | 25 attempts (13 + 12) | 5 |
| `assure-w6`, `w7` | Codex + verify | 25 attempts (13 + 12) | 5 |

**Phase 2 matrix (8 lanes, about 3.5 h).**
- Round 1 is S1 S2 S3 S4 K1 K2 K3 in four rotations.
- Round 2 is V1 followed by round 1 in the same order.
- The Codex arms run 5 attempts × 8 problems each, on 2 lanes per arm.

**Per-unit quota estimates** (weekly-%, from `luna_reuse_DECISIONS.md` "Quota: final reading"; good to about ×2):
- SDO stage: 0.13%. A composite stage is counted at 1.5×, 0.195%.
- Codex attempt: 0.07%. Composite 0.105%. Verify ×1.3.
- SDO lifecycle: 0.3%.
- A warm exact repeat skips reflection and costs less; we still budget it at 0.13%.

| Item | Phase 1 | Phase 2 |
|---|---|---|
| SDO pipelines | 4 × 2 × (3 × 0.13 + 2 × 0.195) = **6.2%** | 4 × [2 × (4 × 0.13 + 3 × 0.195) + 0.13] = **9.4%** |
| Codex stock | 15 × 0.07 + 10 × 0.105 = **2.1%** | 25 × 0.07 + 15 × 0.105 = **3.3%** |
| Codex + verify | 2.1 × 1.3 = **2.7%** | 3.3 × 1.3 = **4.3%** |
| Smoke before launch (1 SDO 2-stage, 1 per Codex arm) | 0.5% | 0.3% |
| Lifecycles (contingency in P1; C10 in P2) | (1.2%) | 0.9% |
| LLM fastloop development (cap) | 1.0% | 1.0% |
| **Planned total** | **12.5%** (13.7% with contingency) | **19.2%** |
| **Worst case (×2)** | 27% | 38% |

**Quota gates.**
- **Hard stop:** the runner does not start a run at ≥ 97% (`run.sh` `QUOTA-STOP`).
- **Phase-1 start:** only after the 2026-10-03 18:19 UTC reset, and only if `used_percent` ≤ 50%.
- **Phase-2 start:** only if `used_percent` ≤ 97 − 38 = **59%** after phase 1. Otherwise run the phase-2 delta only: S4, V1 and K3, round 1 + round 2, 2 rotations, about 8%.
- **Per-lane abort:** stop a lane whose running total exceeds 1.5× its row above. That points to a loop or retry storm.

## (e) Fastloop (no quota) vs full SREGym

| Work | Where | Quota | Why |
|---|---|---|---|
| Qualification of every single and composite problem: inject, oracle fails, recover, oracle passes (3×). The composite's healthy-state diff is compared with the catalog's expected diff | fastloop worker, no agent | none | Proves the problems and oracles before any LLM run |
| C8 gate check: for each composite, apply a scripted partial fix (recover one component). `sdo incident status` must stay unhealthy. Then the full fix must clear it (3 trials) | fastloop plus the SDO controller, no responder | none | Mechanism-level assurance that partial fixes cannot close |
| C6 detector retrieval replay with frozen memory | fastloop plus the controller in observe-only mode | none | The paper's retrieval table, isolated from agents |
| C9 idle-cost soak, 60 min × 3 | fastloop plus the controller | none | Tokens scale with incidents, not time |
| Composite prompt and handling dev iterations (SDO or Codex on K1–K3) | fastloop with agent, no judge | ≤ 1% per phase | Fast debugging without the judge |
| **All headline arm comparisons** (C1–C5, C7, C8 rates, C10, C11) | full SREGym (conductor, LLM judge, persistent pipelines) | as in (d) | Diagnosis correctness needs the judge, and the headline must use the benchmark's grading |

**Requirements for the fastloop implementer** (another agent):
- Composite IDs go through `inject`, `oracle` and `recover` unchanged. The worker's `problem_factory` already uses `ProblemRegistry.get_problem_instance`.
- Expose the per-component oracle results (the `oracles` list in `CompoundedOracle`'s result) in `OracleVerdict`, so a partial fix can be reported per component.
- Add a "recover one component" operation, for example `recover` with `component_index`, for the C8 gate check. The composite class exposes `recover_component(index)`.
- Add an observe-only controller mode (no dispatch) for C6 and C9, if it does not exist yet.

## Preconditions before the live matrix

1. **The robust feedback loop is on main.** It covers traffic detectors, the incident-status gate, the healthy-state diff, evidence-verified diagnosis and helper cleanup. It is on `vic/exp/feedback-loop-e2e`, not yet on `main` as of 2026-09-28. The SDO arm must run a main that includes it, or the TOMLs must run from that branch. This is recorded in the run log.
2. The SREGym pointer includes `vic/feat/composite-problems`, and every problem has qualified in the fastloop.
3. Images are rebuilt: `sregym-agent-base` with the prompt-appendix hook, the SDO images from the run commit, and the lifecycle seed re-attested.
4. The smoke row in (d) passes: the rollouts show `effort: medium`, and the verify arm's first message ends with the protocol.

## Decisions log

- **D1. Composite problems live in the SREGym fork, not in our adapter.**
  - Both SREGym's conductor and the fastloop worker build problems in the SREGym process through `ProblemRegistry`.
  - A class in our adapter could reach that process only through a plugin hook that imports our code into SREGym's venv, which blurs the process boundary the architecture keeps.
  - The fork gets the generic mechanism, the named table as JSON data, and one decoy injector. The assurance metadata stays first-party, cross-checked by a test.
  - Alternative rejected: reviving the upstream `MultipleIndependentFailures`. Its `CompositeApp` is not an `Application`, so source deploy, workload and `app.name` checks break for a single-app composite. It also recovers in injection order, raises nothing on a failed recovery, and has no decoy role.
- **D2. Named composite IDs** such as `composite_<what>_hotel_reservation`, rather than a grammar like `a+b`.
  - Problem IDs become run directory names and CSV keys, and they are checked against the registry's key list by `main.py`.
  - A parsed grammar would need changes in each of those places. Named IDs need none.
- **D3. One decoy kind, `benign_env_drift` (`LOG_LEVEL=debug`).**
  - It is harmless by the source (`tune/setting.go`), shows up in the state diff, and its name reveals nothing to the agent.
  - Alternative rejected: an orphan Service or a crash-looping pod. Those trip generic health detectors and the default `MitigationOracle`, which checks every pod in the namespace. The "decoy" would then become a second fault.
- **D4. Phase-1 problems all have live history, or are composed of components that do.** The one new mechanism, composites, is qualified in the no-LLM fastloop first.
- **D5. 4 SDO pipelines vs 5 Codex attempts per problem.**
  - An SDO pipeline yields a cold and a warm sample per problem.
  - Four cyclic rotations balance stream position.
  - Codex attempts are cheap and independent, and a fifth attempt tightens the baseline median most.
- **D6. Warm repeats are budgeted at the cold rate** (0.13%), even though they usually skip reflection. The per-unit estimates are good only to about ×2.
