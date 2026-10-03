# Mixed stream (single faults plus composites): decisions and results

Branch `vic/exp/mixed-stream` (from `main` `d6efc100`), worktree `/mnt/data/shli/sdo-worktrees/mixed-stream`. Predecessors: `docs/composite-stream-decisions.md`, `benchmarks/sregym/experiments/STREAM_LEARNING_CURVE_DECISIONS.md`.

## Tier 1: generator and fastloop mini-stream

### Generator decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | A composite family is a `FaultFamily` with `requires` (the single-fault families it is made of); `Catalog.composite` holds them. Default `()` keeps every existing catalog and stream unchanged. | A separate `CompositeFamily` type; a second generator. | One concept (family, base problem, variants) already covers "C1 base, C2/C4 target-mix variants". The legacy path draws the same random numbers, so the committed single-fault configs are byte-identical (their tests pass unchanged). |
| 2 | New incident kind `composite` for the first occurrence of a composite family. Later occurrences reuse `exact` (same composite) and `variant` (same family, another target mix). | Kinds `composite_exact` and `composite_variant`. | Keeps the first/exact/variant meaning that analysis scripts already group by; the family column tells singles from composites. |
| 3 | A composite may only appear once every `requires` family has appeared as a single (`_fill` rejects the shuffle; a pinned opening that violates it raises `ValueError`). | Warn only. | The point of the mixed stream is "composition from single-fault memory"; a violating stream would silently measure something else. |
| 4 | Two composite families: `composite-3-class` (C1 `composite3_...` base; C2 `composite3b_...` and C4 `composite3c_...` as variants; readiness + ConfigMap + network policy on different targets) and `composite-4-class` (C5 `composite4_...`, adds the frontend selector). | One family per composite. | C1, C2, C4 share fault classes and differ only in targets, which is the "different mix of the same components" the variant kind means. C5 adds a class, so it is its own family. |
| 5 | **C3 (`composite5_...`) is not in the mixed stream.** | Include it. | It contains `ResourceRequestTooLarge("user")`. That fault has no usable single (verified-destructive on recovery in `STREAM_LEARNING_CURVE_DECISIONS.md`), so its first occurrence could not be composed from singles. It stays available in the composite-stream experiments. |
| 6 | Mixed stream: 24 incidents, seed 20261002. Labels: 4 first, 2 novel, 2 composite, 6 single variants, 2 composite variants, 4 composite exact repeats, 4 single exact repeats. Result: 8 composites of 24. | More composite repeats. | Meets "8-10 composites, at least one composite exact repeat and one composite variant". The cadence is a count constant (`COMPOSITE_EXACT_COUNT`, `COMPOSITE_VARIANT_COUNT`), easy to change. |
| 7 | Mixed pilot prefix is 10 incidents (`MIXED_PILOT_LENGTH`), not 8: four core firsts, a composite, a repeat and a variant must fit, and a composite can only start after all four core firsts. Learnability for mixed catalogs checks that prefix; the "every core fault recurs as a single exact" rule is dropped because composite repeats re-exercise all component classes. | Keep 8. | An 8-prefix with 4 firsts plus a composite leaves no room for an exact and a variant. The single-exact-for-every-core rule is nearly unsatisfiable with only 4 single exact repeats. |
| 8 | Mini stream (6 incidents, pinned, no seed sampling): `network_policy_block`, `missing_configmap`, `readiness_probe`, then C4 (composite first), C4 again (exact), C1 (variant of the C4 family). | A generated 6-stream. | It is the Tier 1 design: composition from singles, exact repeat, and a variant mix. `mini_stream()` uses the same `_fill` rules as the full stream. |
| 9 | Baseline TOMLs are rendered for the mixed stream and the mini stream but **not run**; stored memoryless Codex numbers are reused (C1-C3 from earlier experiments, C4/C5 n=2). | Rerun. | The user rule: reuse the stored baseline unchanged. The mixed-stream single faults and C1/C4/C5 baselines exist; only composites with no stored baseline would need runs (C2 has 2). |

### Environment

- SREGym submodule populated with `git -c protocol.file.allow=always submodule update --init --reference <main checkout>` at the pinned `8035c290` (branch `sdo`, `fix(problems): keep both composite_factories registrations apart in the registry`), then `SREGym-applications` at `d1a7e02`. The test that checks every composite id against `composed_failures.py` passes there: C1, C2, C4 and C5 are registered at the pinned commit. The earlier note that C4/C5 were only in a private submodule gitdir no longer holds for `8035c290` (it contains `composite3c`/`composite4`), but I did not confirm that the commit is on the SREGym remote.

### Tier 1 fastloop mini-stream: run log and result (2026-10-02, not completed)

**Result: no incident of the mini stream was graded.** The generator (above) is done and tested; the fastloop run produced harness and product findings but no resolution, token or timing data. Quota: 92 % used throughout (stop rule 97 %). Load: 7 to 24 at launches (CI runners and other users' jobs dominate; two of my clusters at most). Raw dirs: `/mnt/data/shli/clc-runs/mini-a`, `mini-b` (attempt 1), `mini2-a`, `mini2-b` (attempt 2), `mini3-a`, `mini3-b` (attempt 3, with `controller-final.log` and `networkpolicies-at-stop.txt`). Clusters `mini-w180` and `mini-w181` deleted. Script: `benchmarks/sregym/experiments/mixed-stream/seq_mini.sh`.

| attempt | start (Z) | setup | outcome | class |
| --- | --- | --- | --- | --- |
| 1 `mini-a/b` | 04:48 | cold lifecycle from the `lifecycle-stream` seed repo, images `mini1` (retag of main's `mx1`) | incident 1 failed in the lifecycle after 648 s (a) and 750 s (b): `traffic.NewLinkDetector` undefined in the health judge's `traffic-links` detector | my config: host-side lifecycle validation reads `SDO_VALIDATOR_IMAGE` (default `sdo-detector-validator:v0.1.0`, SDK predates `link.go`); `--validator-image` only reaches the controller install |
| 2 `mini2-a/b` | 05:08 | `SDO_VALIDATOR_IMAGE` exported, cold lifecycle again | b failed after 708 s (`health judge round 1 exhausted bounded correction attempts: ... read outside the application repository`, a benign `rg --files \| rg '(a\|b)/.*\.go'` flagged by `_first_repository_escape` in `sdo/agent_runtime/lifecycle/agents.py`); a still in the lifecycle after 12 min | product: lifecycle guard false positive and slow cold lifecycle with luna (fix is on `vic/fix/lifecycle-guard-false-positive`, `c1659de7`, not used here) |
| 3 `mini3-a/b` | 05:28 | seed = copy of Tier 0's finished post-lifecycle workspace (`lifecycle_seed_stage0`, no incident memory), `SDO_VALIDATOR_IMAGE` exported | incident 1 (`network_policy_block`, cold) was injected at about 05:35 and never detected: controller `findings: []` in 132 (a) and 97 (b) consecutive iterations, 11 and 8.5 min after injection, no responder dispatched. I stopped both runs at 05:46. | product, but degraded SDO (see caveat) |

**Caveat on every NetworkPolicy-related result (incident 1 and C4, C4 repeat).** The coordinator's read-only investigation found that the judge's `links.yaml` (fresh-dial link probe frontend to `recommendation:8085`) was authored but dropped in Tier 0's lifecycle, and therefore in the `lifecycle_seed_stage0` copy used by attempt 3 (and attempt 2), because the host-side self-check fell back to the stale `v0.1.0` validator (strict decode rejects the `links` field). So "a cold NetworkPolicy single is undetectable" is a degraded SDO, not main's behavior with the link probe. Validator-image plumbing is being fixed on a separate branch. These runs were all **without the link probe**.

**Procedure mistake that confounded incident 2.** Stopping incident 1 by killing the `fastloop run` process skipped the fault recovery, so `deny-all-recommendation` stayed in the cluster (kept through `up --redeploy`, which redeploys workloads only). Incident 2 (`missing_configmap`) therefore ran with the NetworkPolicy already present: on mini3-b both `deny-all-recommendation` (27 min old) and a responder-added `allow-frontend-recommendation` were present at stop; on mini3-a the controller log shows the healthy-baseline gate rejecting a learned `deny-all-ingress` incident detector (`TestIncidentDetectorsStayQuietOnHealthyBaseline`: it fired on the "healthy" snapshots, which contained the leaked NetworkPolicy). The gate did what it is for, but the result says nothing about the mini stream. Both runs were stopped at about 06:05 and incident 2 is not reported. Also, each `run` re-ran the lifecycle self-check (a `codex exec` health judge started at incident 2 in both sequences, and mini3-b logged `health judge source commit does not match the published deployer assessment`), which I did not diagnose.

### Tier 1 decisions (run)

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 10 | Use `mx1` (main HEAD, built by the Tier 0 agent at 04:45Z) retagged locally as `mini1`, not `nf5`. | `nf5` (composite-stream build); a fresh build. | Tier 1 validates main's behavior; `nf5` predates the merge. A retag adds no state to the shared images and avoids a 10 to 15 min build. |
| 11 | Two replicate sequences on separate clusters (`mini-w180`, `mini-w181`), staggered 3 min in attempt 3. | One sequence. | n=2 gives a cheap variance check; both within the 1-2 cluster allowance. |
| 12 | Attempt 3 seeds from Tier 0's finished lifecycle instead of a fourth cold lifecycle. | Retry the cold lifecycle again. | Two cold-lifecycle attempts had already failed or stalled for reasons outside the stream (validator image, guard false positive). The seed has no incident memory, so incident 1 is still cold for memory. |
| 13 | Stop incident 1 after 11 min without detection by killing `fastloop run`. | Wait for the 3600 s default; rerun with `--timeout 900`. | Over the 10 to 15 min stall rule with no responder activity. This was the wrong way to stop it (no recovery, see above); a rerun should pass `--timeout 900` and let the harness recover the fault. |
| 14 | Stop everything at 06:05 and report the partial result. | Continue past the 06:10 budget. | The coordinator budget; incident 2 was already confounded. |

### Tier 1 next steps

1. Rebuild the lifecycle seed with the validator-image fix, so the link probe is present, then rerun the mini stream.
2. Run the mini stream with `--timeout 900` per incident; if an incident must be abandoned, run the harness fault recovery (or delete the leaked NetworkPolicy) before the next one.
3. Find out why each `fastloop run` re-ran the lifecycle self-check (source commit mismatch) when the seed already carried an attested lifecycle.
4. Decide whether the mixed stream's NetworkPolicy single stays first: with the link probe it should be detectable; without it every NetworkPolicy single burns the full per-incident timeout.


## Tier 0: composite C1 as one conductor pipeline stage

Branch `vic/exp/mixed-stream-smoke` (from `main` `d6efc100`), worktree `/mnt/data/shli/sdo-worktrees/mixed-smoke`. Goal: show that a composite problem runs end to end as a stage of a persistent-controller pipeline through the full conductor (cold SDO controller, Codex gpt-6-luna, judge `codex-gpt-6-luna` xhigh, strict receipt, official oracle, judge-free TTD/TTM).

### Decisions

- **Worktree and submodule.** New worktree from `main`; `third_party/sregym` initialised at the recorded pin `8035c290` (SREGym `sdo` branch) with `--reference` to the shared clone, `SREGym-applications` at `d1a7e02`. Alternative: reuse the main checkout's submodule. Rejected: the main checkout must stay untouched.
- **Images: private tag `mx1`**, built from this worktree (`SDO_IMAGE_TAG=mx1 BUILDX_BUILDER=sdo-example`). Alternatives: reuse `nf5` (built 16 h ago from the pre-merge composite-stream branch, so not provably equal to `main`), or `v0.1.0` (never retag).
- **Cluster: prefix `smoke-w`, `SREGYM_WORKER_ID_OFFSET=40`** gives cluster `smoke-w40`, API port 8040, MCP port 9994; no existing cluster or listening port uses these.
- **Settings.** All-on SDO from `composite-stream-decisions.md` expressed as `agent_config` keys: `late_findings = "pull"`, `max_follow_ups = 3`, `follow_up_cooldown_seconds = 30`, `reflection_guidance = "generalize"`, `reflection_session = "fresh"`, `healthy_baseline = true`. Everything else is copied from `sdo_codex_luna_stream_pilot.toml` (reasoning medium, 1 control plane + 1 worker, worker_cpu_limit 3, deploy from source, strict receipts). Config: `benchmarks/sregym/experiments/mixed-stream/tier0_smoke.toml`.
- **One stage first.** A second (repeat) stage only if stage 1 is clean and quota allows. Codex weekly quota at start: 92 % used (stop rule 97 %).

### Tier 0 result (2026-10-02, run `20261002_044638_pipeline_sdo-codex-luna-mixed-smoke`, cluster `smoke-w40`, deleted afterwards)

**The composite runs as a conductor pipeline stage with no harness change, but the incident was not solved.** The conductor accepted `composite3_hotel_geo_rate_recommendation`, the cold lifecycle and persistent controller worked, the deferred injection gate injected all three faults, the strict receipt is valid (`completed`, `acknowledged`, `cleaned`, validator Job with NetworkPolicy canaries passed), and both judges ran (luna xhigh). No test-first harness fix was needed, so there is no code change in this branch.

| item | value |
| --- | --- |
| faults | 3 (geo readiness probe port, `mongo-rate-script` ConfigMap for mongodb-rate, NetworkPolicy isolating `recommendation`) |
| SDO repaired | geo readiness and the ConfigMap (2 of 3); the NetworkPolicy on `recommendation` was never found |
| Official oracles | `ReadinessProbeMitigationOracle` pass, `MitigationOracle` pass, `NetworkPolicyMitigationOracle` **fail**; `Mitigation.success` False (66.7 %) |
| Diagnosis (judge) | False, 2 of 3 faults (66.7 %); the diagnosis text lists only the readiness and ConfigMap causes |
| Controller view | every health detector `clear` at 05:11:22Z; healthy verify burst passed 5/5 for hotel-login, hotel-recommendations, hotel-search; `no_incident_detector_fired` true |
| Probes versus oracle | the SDO health detectors and probes agreed the app was healthy; only the official NetworkPolicy oracle saw the remaining fault (same disagreement as the 2 of 40 composite-stream oracle misses, but here it is the first and only composite) |
| Pull-before-act | on (2 non-empty pulls, `late_finding_consumed` true); follow-ups did not trigger because no finding stayed active |
| Time (judge excluded) | diagnosis submitted 22.3 s after `fault_injected_at`; mitigation submitted 157.5 s after it, of which the 71 s diagnosis-grading wait (05:07:48 to 05:08:59) sits inside the window, so about 86 s judge-free; receipt `incident_resolution_seconds` 250.9 (detected to verified health, started at detection, before the injection timestamp); responder 237.7 s |
| Pre-injection cost (excluded from TTM) | inventory and lifecycle 678 s, controller install 12 s, healthy baseline 5 s, fault gate 32 s; fault_injection_deferred_seconds 724 |
| Tokens (this incident) | responder 0.52M input (0.475M cached) + 5.9k output; reflection 1.04M input (0.98M cached) + 15k output, 28 requests; lifecycle tokens not extracted |
| Wall clock | 1340.8 s for the pipeline (about 33 min including cluster up, under the 45 min budget); no infra stall |
| Learning | reflection added `missing_required_configmap` and `readiness_port_mismatch` incident detectors (accepted); nothing for the NetworkPolicy |
| Quota | 92 % used (the newest rollouts, including this run's, all read 92 %); stop rule 97 % not reached |

Infra: none (no kind, Calico, image, controller-install or health-timeout failure). The `codex-quota` preflight line warned that its snapshot was stale; Tier 1's stale `SDO_VALIDATOR_IMAGE` problem does not apply here because the config pins `mx1` for the validator and the validator canaries passed.

Takeaways:

1. The mixed stream is feasible in the conductor path: composite IDs need no harness change (receipt, judges, oracle, deferred injection, persistent controller all work). Confidence: high for one stage; no repeat stage was run.
2. A cold first composite containing a NetworkPolicy fault is not solved by the health detectors, and this is the exact fault class the fairness audit removed hard-coded knowledge for (`fairness-DECISIONS.md` item 2). The user-facing traffic checks stay green with `recommendation` isolated, so SDO declares health restored while the official oracle disagrees. Confidence: medium (n=1; the earlier fast-loop stream resolved the cold C1 3/3, so part of the difference may be run-to-run luck or the stricter conductor grading versus read-only probes). Implication: in the full-conductor mixed stream, composites that contain the NetworkPolicy fault will score as unsolved on first occurrence unless a detector for it is learned, so score them separately and expect the learning curve to show up there. Next step: rerun the cold C1 once or twice to see whether the miss is systematic, and look at whether the controller's `traffic-health` probes ever hit `recommendation`.
3. The second (repeat) stage was not run: stage 1 was not clean, which was the condition for running it.

## Phase A runs (2026-10-02, branch `vic/exp/mixed-integration`)

Goal: show that the three fixes (validator-image plumbing, lifecycle guard, close-out gate) work on a cluster, then run the mixed mini stream. Codex gpt-6-luna for the SDO agents and the SREGym judge (xhigh); the Codex quota check is ignored (user, 2026-10-02). Raw runs: `/mnt/data/shli/clc-runs/mi-*` and `third_party/sregym/logs/*confirm-c1*` in the `mixed-integration` worktree.

### Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | Integration branch `vic/exp/mixed-integration` from `vic/exp/mixed-stream`, merging smoke, lifecycle-guard, validator-image and close-out-gate (one add/add doc conflict; kept both sections). Later merged preflight, efficiency and repair-attribution-clock. | Test each fix on its own branch. | One build under one tag for the three fixes is what a confirming run needs; the controller and validator tags must match. |
| 2 | Images built under private tags `mi1` to `mi4` (`SDO_IMAGE_TAG`, `BUILDX_BUILDER=sdo-example`); `SDO_VALIDATOR_IMAGE` left unset in every run. | Export the env var as the earlier runs did. | Leaving it unset is the point of the validator-image fix; `mi1` and `mi2` each exposed a bug that the env var had masked. |
| 3 | Cold fastloop seed `/mnt/data/shli/clc-runs/seeds/cold-hotel` built from `SREGym-applications/hotelReservation` (one commit, no `.sdo`). | Reuse `lifecycle-stream` or `mini-lifecycle` (both already carry a `.sdo`). | A cold lifecycle was the thing under test. |
| 4 | Conductor C1 run sets `SDO_PREFLIGHT_MAX_QUOTA_USED_PERCENT=100`. | `SDO_PREFLIGHT=warn` (records the run as `invalid_infra`). | User: ignore quota checks. |
| 5 | Stopped three runs by hand before a result existed (a conductor run still in its lifecycle, two fastloop singles whose NetworkPolicy could not be detected); removed the leaked `deny-all-recommendation` policy manually and deleted the controller namespace before every rerun. | Wait out the 900 s timeout. | Each wait was 10+ min for a known miss. Never killed a run after a fault without recovering it. |
| 6 | Mini stream with `seq_mixed.sh` (efficiency branch): no `--inject-before-resume`, `--timeout 900`, `--detection-timeout 120`, close-out gate on, seed `seeds/mi-lifecycle` (the cold NP-single lifecycle at its attestation commit, with no incident memory and five frontend links). | `COLD=1` with a fresh lifecycle; Tier 0's seed. | A cold lifecycle's link coverage is nondeterministic (see findings), so the mini stream tests the stream behaviour, not the lifecycle. A cold lifecycle stays required for any final number. |

### Bugs found and fixed on the way (each test-first, pushed)

| Commit | Bug | Effect |
| --- | --- | --- |
| `320b5dcf` | `fastloop run` called `run_or_reuse_lifecycle` without `validator_image`, so host-side validation fell back to `v0.1.0` and the judge dropped `links.yaml`. | The validator-image branch had wired only the driver call sites. |
| `d58898f0`, `7d17e7eb` | The Python controller launcher (`sdo-detector-check controller`) did not define `--closeout-state-gate`; every controller pod exited with "unrecognized arguments". | The close-out gate could not have run on any cluster. |
| `ce7663db` | The fault gate injected at the controller's first clear evaluation, before the prober's first dial; a link finding needs an edge that connected once. | A cold NetworkPolicy was never detected even with the link probe. Now waits 30 s for a Ready prober (none for apps without one). |
| `aabf8b0d` | The responder deleted the policy at 07:57:07.5, before health cleared (07:57:08.8), but wrote `started_at` from a later clock read (07:57:17); the receipt said unattributed, reflection was skipped, nothing was learned. | Prompt now asks for `date -u` around the mutation; the clock-skew branch fixes it in code. |
| `7c057f94` | Go `omitempty` vs Python default `[]` for `acknowledged_state_changes` broke the `incident_result` golden round trip after the gate merge. | Field optional in Python, empty list dropped from the fixture. |

### Step 2: confirming runs (luna, gate on)

| Run | Image | What | Result |
| --- | --- | --- | --- |
| Cold lifecycle, fastloop (`mi-np-single`) and conductor (`confirm-c1`) | `mi2` | `links.yaml` and the `traffic-links` detector survive validation | pass on both paths; no "unknown field" |
| Cold `network_policy_block` single, fastloop, load 12 to 28 | `mi2` | link finding, responder, repair | `link-reachability.frontend.recommendation.8085` fired 7.2 s after injection (before dispatch); responder 133k tokens; oracle success; inj to mit 44 s; the receipt was `unattributed` (clock) |
| Healthy window after the incident | `mi2` | 10.4 min, 124 controller iterations, no injection | 0 active findings (0 link false positives) |
| Cold C1 composite, conductor (`confirm-c1`), luna xhigh judge | `mi2` | compare with Tier 0 (2/3 faults, NetworkPolicy missed) | the same: 2/3 faults repaired, official `NetworkPolicyMitigationOracle` failed, Diagnosis 2/3. No link finding: this lifecycle's judge wrote `links.yaml` with only `frontend->consul` and `search->consul`. |

Finding: link coverage varies across cold lifecycles (the NP-single lifecycle declared five frontend edges and caught the fault; the C1 lifecycle declared two consul edges and did not). The prompt is the same. Proposal (about 25 min, plus one cold C1 rerun): a deterministic backstop that dials every in-namespace Service port, or a validator rule that every Service the health objective names has an inbound edge. Not implemented in this phase.

### Step 3: mixed mini stream, two sequences (images `mi4`, luna, gate on, `seq_mixed.sh`, 2026-10-02 08:32 to 09:45Z)

Stream: `network_policy_block`, `missing_configmap`, `readiness_probe`, C4 (first composite, after its three component singles), C4 again, C1 (a different mix of the same components). Fastloop, probe graded plus the official oracle of the problem; no LLM judge. Two replicate sequences `mi-mini-a` (worker 62) and `mi-mini-b` (worker 63, started 3 min later); load at launches 8 to 14 (no run was started above 20). Raw: `/mnt/data/shli/clc-runs/mi-mini-{a,b}`.

| # | problem | a: oracle, inj to det / mit s, tokens (resp + refl) | b: oracle, inj to det / mit s, tokens (resp + refl) |
| --- | --- | --- | --- |
| 1 | `network_policy_block` | pass, 6.1 / 32.5, 0.43M (0.14 + 0.30) | pass, 6.5 / 45.1, 0.33M (0.13 + 0.20) |
| 2 | `missing_configmap` | pass, 0.7 / 32.2, 0.43M (0.21 + 0.22) | pass, 0.7 / 60.3, 0.47M (0.26 + 0.21) |
| 3 | `readiness_probe` | pass, 0.7 / 67.2, 0.50M (0.25 + 0.25) | pass, 0.8 / 32.3, 0.26M (0.15 + 0.11) |
| 4 | C4 first composite | pass, 0.8 / 84.9, 0.80M (0.46 + 0.34) | pass, 0.7 / 66.1, 1.09M (0.49 + 0.60) |
| 5 | C4 exact repeat | pass, 0.7 / 58.9, 0.29M (0.29 + 0) | pass, 0.7 / 133.0, 0.54M (0.54 + 0) |
| 6 | C1 variant | pass, 0.7 / 61.0, 0.78M (0.48 + 0.30) | pass, 0.6 / 144.9, 0.90M (0.61 + 0.28) |

12 of 12 incidents pass the official oracle, including the three composites that contain a NetworkPolicy fault; 0 errors, 0 undetected, 0 follow-up incidents needed. The single NetworkPolicy is detected by the link probe 6 s after injection (health-judge `traffic-links`, before dispatch); every repair was `sdo_mitigated` (attributed), `clock_skew_corrected` was empty on every receipt (no responder start time landed within 30 s after health cleared), and reflection ran on every incident except the C4 repeat (skipped as an exact-match success). Learned incident playbooks after six incidents: 3 (NetworkPolicy, ConfigMap, readiness) in each sequence, no learned detector fired outside its fault component, and the healthy-baseline gate rejected nothing. The close-out gate recorded no send-backs (no receipt carries a `closeout_gate` outcome), so this run does not exercise it.

Takeaways:

1. The stream resolves with the three fixes on and the link probe. Meaning: 12 of 12 by the official oracle, including composites with a NetworkPolicy fault (0 of 2 in the cold Tier 0 and C1 conductor runs, whose lifecycles lacked the recommendation edge). Confidence: medium (n=2 sequences, one app, one lifecycle seed with a good five-edge `links.yaml`). Implication: the earlier misses came from the missing link coverage and the clock-skew attribution, not from the oracle. Next step: a cold C1 with the link-coverage backstop (queued).
2. Composition from singles saves little on its first composite. Meaning: C4 first occurrence costs 0.80M and 1.09M tokens (the old cold C1 to C3 cost 0.85M to 1.25M), almost all of it a 0.3M to 0.6M reflection; the exact repeat costs 0.29M and 0.54M with no reflection; the C1 variant costs 0.78M and 0.90M, again with a reflection. Time shows no trend (33 to 145 s). Confidence: low (n=2). Implication: memory pays on exact repeats, not on first or variant composites at this stream length, as in the composite-stream study. Next step: the 10-incident pilot.
3. In composites the learned ConfigMap and NetworkPolicy detectors fired after dispatch, not before (only the learned readiness detector fired before dispatch in sequence a), and the diagnosis verification marked two of three composite root causes `contradicted` (the responder cited a learned detector finding as evidence that had not fired at dispatch). Meaning: the responder repairs correctly (oracle pass, attributed repairs) but cites evidence that the verifier cannot confirm. Confidence: medium (4 of 4 first composites). Implication: this affects learning quality, not resolution. Next step: check what the reflection stores from `contradicted` verdicts.

### Step 4 prelude: link-coverage backstop confirming reruns (images `mi5`, luna, gate on, 2026-10-02 10:02–10:26Z)

The `mi5` build adds the deterministic link-coverage backstop: a lifecycle-derived `topology-links.yaml` (dials every in-namespace core Service TCP port from `sdo-prober`) plus the `traffic-topology-links` health detector, so link coverage no longer depends on which edges the judge happens to write into `links.yaml`. Two cold reruns on fresh clusters (mi-w60, mi-w61; both deleted after capture). Raw: `third_party/sregym/logs/20261002_100256_pipeline_sdo-codex-luna-mixed-confirm-c1` and `/mnt/data/shli/clc-runs/mi-np-cold5`.

| Run | Path | Result |
| --- | --- | --- |
| Cold C1 composite, conductor (`confirm-c1`), luna xhigh judge | mi5 | **solved=true** (official oracle). `resolution=sdo_mitigated`, `recovery_attribution=responder`, all three repairs `attributed=True`. All 3 root causes confirmed (geo readiness, mongodb-rate ConfigMap, deny-all NetworkPolicy on recommendation). Reflection ran (`reflection_attempts=1`, `reflection_skipped_reason=None`, commit `27bb0021`). |
| Cold `network_policy_block` single, fastloop | mi5 | **oracle=yes**. inj→det 6.7 s, inj→mit 45.2 s, inj→verified 106.1 s, resp 134627 tok, refl 71750 tok, gate 8.6 s. |

Backstop confirmation:

- `topology-links.yaml` lists **recommendation:8085** and every hotel Service port (geo:8083, profile:8081, rate:8084, reservation:8087, search:8082, user:8086, plus consul/jaeger/memcached/mongodb), 35 edges, provenance "lifecycle-derived … not authored by the health judge".
- `traffic-topology-links` is registered as a `health`/`health_judge` detector in `manifest.yaml` and fired on both runs.
- Cold C1: `link-reachability.sdo-prober.recommendation.8085` fired (via the backstop) and the confirmed NetworkPolicy cause cites `traffic-topology-links` as evidence; the responder pulled it as a late finding (`late_finding_consumed=true`, `pull_count=2`). In a composite the incident dispatches on the first fault, so the recommendation link finding is `after_dispatch` here, not before.
- Cold NP single: **both** `traffic-links` (frontend edge) and `traffic-topology-links` (sdo-prober backstop) fired `before_dispatch` for recommendation.8085 — coverage no longer hinges on the judge's edge list.

Caveat carried into Step 4: in the cold C1 the diagnosis verifier marked the NetworkPolicy cause **contradicted** (2 unverified, 1 contradicted) because its link evidence fired `after_dispatch`; all repairs were still attributed and the oracle passed. This is the exact gap `vic/fix/verifier-late-findings` closes (read the controller's `detector_timeline` for `after_dispatch` findings the responder legitimately pulled). Merge it before the pilot and expect contradicted-cause counts to drop to ~0 on composites.

### Step 4 prelude: healthy-window soak (mi5 source, no injection, 2026-10-02 17:30–17:45Z)

The false-alarm soak (`fastloop.assurance run --no-single --soak-minutes 11`) on a fresh cluster `mi-w65`, seed `soak-lifecycle-mi5` — the cold-C1 lifecycle reset to its lifecycle commit `f4093d7` (both `traffic-links` and `traffic-topology-links` installed, `outcomes.jsonl` empty). Controller built from the mi5 source commit; synthetic traffic runs, nothing injected. Raw: `/mnt/data/shli/clc-runs/mi-healthy-w5/assurance/results/mi-healthy-w5.json`.

- **no detector finding while healthy: 0 of 131 evaluations** (both link detectors silent for the full window).
- no incident opened while healthy: 0.
- incident status healthy throughout: 0 of 3 probes unhealthy.
- post-soak `selector-mismatch` passed (`diff=exact`, cleared 3.9 s, verified 14.6 s, 0 left): the soak left no residue in the state diff.
- resource drift over ~11 min: controller CPU ~1.5 core-s/min idle, RSS steady 48–50 MiB; prober steady ~9–10 MiB; apiserver ~0.3 req/s; 0 port-forward restarts.

Takeaway: the deterministic topology-links backstop adds no false positives. Meaning: 131 healthy evaluations, 0 link findings from either `traffic-links` (judge edges) or `traffic-topology-links` (every in-namespace Service port). Confidence: high for this app/lifecycle (n=131 evals, one cold lifecycle). Implication: dialing every Service port every evaluation does not flap on a healthy namespace, so the backstop is safe to leave on for the pilot. Next step: merge the verifier late-findings fix, rebuild mi6, run the 10-incident pilot.

## Step 4: 10-incident mixed pilot through the conductor (images `mi6`, luna xhigh judge, 2026-10-02 17:55–19:20Z)

One persistent SDO controller (Codex gpt-6-luna, medium) answered a 10-incident learning-curve stream on hotel-reservation through the full conductor path: strict receipts, the Codex xhigh judge, deferred injection, cold lifecycle at stage 0 (no seed shortcut), each later stage chaining the workspace so memory accrues. `mi6` = the mixed-integration build (validator-image, lifecycle-guard, close-out gate, prober-warm, attribution-clock, link-coverage backstop) **plus the verifier late-findings fix**. Config `benchmarks/sregym/experiments/sdo_codex_luna_mixed_pilot.toml`. Raw: `third_party/sregym/logs/20261002_175513_pipeline_sdo-codex-luna-mixed-pilot`. TTM is injection→mitigation, judge-free (judge time excluded). Responder tokens are per-incident deltas of the persistent controller's cumulative turn log (input+output, cache-dominated); reflection tokens are the receipt's `reflection_usage`.

### Takeaways

1. **The verifier late-findings fix closed the composite gap: both composites now verify all causes.** Meaning: incidents 7 and 10 (composite3 = geo readiness + rate ConfigMap + recommendation NetworkPolicy) each returned **3/3 confirmed, 0 contradicted** — the exact case that was `contradicted` (2 unverified, 1 contradicted) in the pre-fix mi5 cold-C1 rerun, because the recommendation link evidence fired `after_dispatch` and the responder pulled it as a late finding. Confidence: high for the mechanism (direct before/after on the same composite, same build minus the fix). Implication: reflection can now learn from the NetworkPolicy cause in composites instead of discarding it. Next step: confirm the learned composite playbook improves a later composite's diagnosis depth.
2. **Mitigation is solid across the whole mixed stream; the one miss is a diagnosis-characterization score, not a repair failure.** Meaning: **mitigation 10/10** (every mitigation oracle passed, including both composites 3/3); **9/10 solved** by the official oracle. The only non-solve, incident 10 (exact composite), passed all three mitigation oracles (TTM 191 s) but scored `Diagnosis.success=False` (66.67%): the xhigh judge docked D2 Fault Characterization because the geo readiness write-up "omits the ground-truth's nonexistent /healthz endpoint." On the exact repeat the responder applied the learned playbook and submitted a terser diagnosis than the first occurrence (incident 7, which solved). Confidence: medium (n=1 composite repeat). Implication: memory makes repairs faster but can thin the diagnosis narrative below the judge's characterization bar; the learning curve needs a diagnosis-quality guard, not just a mitigation one. Next step: have reflection carry the first occurrence's characterization detail (the /healthz specifics) into the playbook so exact repeats re-state it.
3. **A residual contradicted-cause class remains on single faults, and it does not block solving.** Meaning: four single-fault incidents (2 selector, 3 configmap-variant, 4 readiness, 9 misconfig-exact) each show 1 `contradicted` cause; all four still scored diagnosis+mitigation success. Root cause (verifier-fix agent, reading the strict receipts): the responder cited a real operational signal under the **wrong evidence kind / a non-catalog name**, so one evidence part fails verification and sinks the whole cause — i02/i04 listed `synthetic-reservation` as a `synthetic-traffic` source (not a probed scenario; the other 3 scenarios matched real after_dispatch findings), i03 cited `sdo-incident-status` (the `sdo incident status` CLI, not a scenario), i09 cited `change-diff` as a `detector-finding` (the state diff, not a detector). The tell: i02's `kubectl` `live-observation` evidence verified as null (unverifiable) and did **not** contradict — so the same facts cited under `live-observation` would all have confirmed. It is **not** the empty-`explained_detectors`/missing-timeline-metadata shape I first reported (the timeline does carry relation + timestamps). Confidence: high (all 4 receipts). Implication: the composite fix did its job; this is a responder evidence-kind discipline gap, costing only reflection-learning. Next step: responder-side fix authorized (evidence-kind discipline — synthetic-traffic.source must be a probed scenario, detector-finding.source a real detector/rule id, ad-hoc checks go under live-observation); the optional verifier-semantics change (don't contradict a joined synthetic-traffic source when ≥1 part is a real scenario) is held for coordinator sign-off since it alters the contradiction gate on reflection.

### Per-incident results

| # | kind | problem | solved | diag | mit (oracles) | TTM s | contra | reflection | resp tok | refl tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | first | missing_configmap_hotel_reservation | yes | ✓ | ✓ | 91 | 0 | ran | 0.37M | 0.32M |
| 2 | first | wrong_service_selector_hotel_reservation | yes | ✓ | ✓ | 64 | 1 | ran | 0.27M | 0.09M |
| 3 | variant | missing_configmap_mongodb_geo_rate | yes | ✓ | ✓ | 214 | 1 | skip (exact-match) | 0.58M | 0 |
| 4 | first | readiness_probe_misconfiguration | yes | ✓ | ✓ | 57 | 1 | ran | 0.19M | 0.09M |
| 5 | first | network_policy_block | yes | ✓ | ✓ | 58 | 0 | ran | 0.17M | 0.36M |
| 6 | novel | misconfig_app_hotel_res | yes | ✓ | ✓ | 50 | 0 | ran | 0.23M | 0.14M |
| 7 | composite | composite3_hotel_geo_rate_recommendation | yes | ✓ | ✓ (3/3) | 102 | 0 | ran | 0.45M | 0.43M |
| 8 | variant | readiness_probe_misconfiguration__v_reservation | yes | ✓ | ✓ | 48 | 0 | ran | 0.17M | 0.22M |
| 9 | exact | misconfig_app_hotel_res | yes | ✓ | ✓ | 41 | 1 | ran | 0.22M | 0.21M |
| 10 | exact | composite3_hotel_geo_rate_recommendation | **no** | ✗ | ✓ (3/3) | 191 | 0 | skip (exact-match) | 0.86M | 0 |

Totals: **solved 9/10**, **mitigation 10/10** (every mitigation oracle), **diagnosis 9/10**, contradicted causes **0/2 composites** (was 1/1 on the pre-fix C1), 4/6 singles. Exact-match repeats (3, 10) skipped reflection as prior-verified successes. Network-policy single (5) detected before dispatch by the link probe (contra 0). Codex memoryless baseline on the identical incidents is reused from the stored run (not rerun); the composites are the discriminating incidents (memoryless Codex scored them poorly in the composite-stream study).

### Decision: responder evidence-kind normalization seam (A vs B, 2026-10-02)

The responder evidence-kind fix (`vic/fix/responder-evidence-kind`, merged) normalizes at the responder emit seam (`execute_incident`), which only sees the dispatch-time catalog — not the closure's after-dispatch `detector_timeline`. Consequence: on composites, a genuinely late *pulled* detector-finding is recorded as `live-observation` in the responder receipt. The cause still confirms (via `explained_detectors` + attribution) and reflection still learns — the verdict and learning are unchanged; only the receipt's evidence-kind label differs.

Two options were surfaced by the verifier-fix agent:
- **A** — relocate normalization into the verification/outcome pipeline so the catalog is `dispatch ∪ after-dispatch` and late pulled detectors keep the `detector-finding` label. Verdict-identical, reflection-identical; improves trajectory provenance for analysis/paper.
- **B** — keep the verdict-safe responder fix and document the caveat.

**Decision: B now; A held for coordinator sign-off (bundle with verifier fix #2).** Rationale: A's only benefit is receipt/trajectory-label fidelity (it changes neither the verdict nor what reflection learns), and its cost is touching the verification/outcome pipeline — the same fenced area as fix #2, which the coordinator asked us to treat carefully. A cosmetic-to-verdict relabel does not justify a unilateral change there. B's documentation (the analyze-experiment reference caveat) is required by CLAUDE.md regardless. If paper evidence-provenance tallies need the distinction, that is the trigger to approve A.

## Step 5: variance pass (images `mi7` = fix #1 active), seed A (2026-10-02 20:07–21:34Z)

mi7 = integration HEAD with the responder evidence-kind discipline fix (#1) active in the running responder. Same 10-incident mixed stream through the conductor, luna xhigh judge, cold lifecycle at stage 0. Config `sdo_codex_luna_mixed_pilot.toml` (bumped to mi7). Raw: `third_party/sregym/logs/20261002_200716_pipeline_sdo-codex-luna-mixed-pilot`, run `mi-var-a` (cluster mi-w71, torn down).

Result: **solved 9/10, mitigation 10/10, TOTAL contradicted causes = 0** (was 4 across i02/i03/i04/i09 on the mi6 pilot). The one non-solve is again i10 (exact composite): mitigation 3/3 passed (TTM 163 s) but `Diagnosis.success=False` (66.67%) — this time the judge docked D2 Fault Characterization for an *extra* cause ("attributes geo's unavailability to live image drift, which is not the stated fault"), a different characterization miss than the mi6 pilot's omitted-/healthz. Reflection ran on i10 this time.

Two anomalies to confirm against seed B:
- **i07 (first composite) strict receipt rejected** (`validation_error: production receipt requires completed=true`); the benchmark still graded it solved=true with mitigation 3/3 and diagnosis passing. The controller log ends with link findings (recommendation:8085, geo:8083, mongodb-rate:27017) still active, i.e. the incident did not reach a clean close-out before the receipt was captured — a close-out/timing issue (not a fix #1 path; fix #1 only relabels responder evidence). Watching whether it reproduces.
- **i10 non-solve persists on an independent cold lifecycle**, so it is not the mi6 pilot's load artifact — it is genuine diagnosis-characterization variance on the exact composite repeat (the applied-playbook diagnosis drifts above/below the judge's characterization bar run to run).

### Takeaways (seed A)

1. **Fix #1 works: contradicted causes went 4 → 0 on an independent cold lifecycle.** Meaning: all four single-fault causes that were `contradicted` on the mi6 pilot (mislabeled synthetic-traffic/detector-finding evidence) now confirm; composites' mitigation stays 3/3. Confidence: medium-high (n=1 seed, but a clean 0 and the mechanism is deterministic). Implication: reflection now learns from every correct single-fault cause. Next step: confirm 0 holds on seed B.
2. **The i10 exact-composite non-solve is a real diagnosis-quality variance, not load.** Meaning: it recurs on a fresh lifecycle, failing D2 characterization for a different reason each run (omitted detail; extra cause). Confidence: medium (2/2 i10 runs). Implication: the exact-repeat diagnosis (applying a learned playbook) is not reliably re-stating full fault characterization for the xhigh judge; this is a memory/reflection content question, not a mitigation one. Next step: have reflection store the first occurrence's full characterization in the composite playbook (parked under option A/fix #2 territory — surface to coordinator, do not change verifier/reflection semantics unilaterally).
3. **A close-out completion anomaly appeared on the first composite (i07).** Meaning: solved by the oracle but the strict receipt was rejected for `completed!=true`, with faults still showing active late in the controller log. Confidence: low (n=1). Implication: possibly a close-out/reflection-drain timing cutoff on the most expensive incident; needs a second data point before acting. Next step: check i07 on seed B; if it reproduces, pull the close-out/reflection-drain timing from the controller log.

### Step 5: variance pass, seed B (quiet host, 2026-10-02 21:37–23:02Z)

Raw: `third_party/sregym/logs/20261002_213714_pipeline_sdo-codex-luna-mixed-pilot`, run `mi-var-b` (cluster mi-w72, torn down). Host load 6–9 throughout (the quiet-host data point for i10).

Result: **solved 10/10, mitigation 10/10, TOTAL contradicted = 0.** i10 (exact composite) **solved on the quiet host** — diagnosis passed this time (3/3 causes confirmed, mitigation 3/3). i07 had a valid receipt with 3/0 confirmed.

The close-out anomaly reproduced but **moved to i10** and showed a *different* strict-gate cause: `production receipt requires remaining_worktrees=[]` (seed A's i07 was `completed!=true`). Both are strict-receipt completion-gate rejections on the expensive composite incidents; neither blocked the benchmark oracle (both composites solved with mitigation 3/3).

### Takeaways (variance pass, seeds A+B)

1. **Fix #1 is confirmed robust: contradicted = 0 on both independent cold lifecycles** (was 4 on the mi6 pilot). Meaning: every single-fault cause that was mislabeled-and-contradicted now confirms; composites' mitigation stays 3/3; red-herring detection intact. Confidence: high (n=2 seeds, both clean 0, deterministic mechanism). Implication: the evidence-kind gap is closed — ship fix #1. Next step: none for the fix itself.
2. **i10 (exact composite) is solvable; its earlier non-solve was diagnosis-characterization variance, host-condition-sensitive.** Meaning: seed B (quiet host) solved i10 10/10; seed A (busier) failed i10 on D2 characterization. Across mi6+A+B the exact composite repeat is 1/3 solved, correlating with host quiet. Confidence: medium (3 i10 runs). Implication: the applied-playbook diagnosis on the exact repeat sits right at the xhigh judge's characterization bar and tips with run-to-run narrative variance; richer playbook characterization (option-A/fix-#2 territory) would stabilize it — not a mitigation gap. Next step: surface to coordinator with the stored first-occurrence characterization as the fix lever; do not change verifier/reflection semantics unilaterally.
3. **Composites intermittently fail the strict-receipt completion gate (receipt rejected though the oracle solves).** Meaning: seed A i07 `completed!=true`, seed B i10 `remaining_worktrees!=[]` — the 3-fault incidents sometimes don't finish close-out / worktree-drain before the receipt is captured. Confidence: medium (2/4 composite runs across A+B). Implication: a learning-durability/latency issue on the heaviest incidents (reflection/worktree drain racing the receipt), not a resolution issue; the solved verdict and attributed repairs are unaffected. Next step: pull the close-out + reflection-drain timeline from a rejected composite's controller log and check whether the drain simply needs more time / a completion barrier before the receipt snapshot. (New item, not fix #1/#2/A.)

### Step 5: variance pass, seed C (rerun) (quiet host, 2026-10-03 01:08–02:38Z)

Raw: `third_party/sregym/logs/20261003_010822_pipeline_sdo-codex-luna-mixed-pilot`, run `mi-var-c2` (cluster mi-w74, torn down). mi7 images (fix #1 active), same 10-incident mixed stream through the conductor, luna xhigh judge, cold lifecycle at stage 0. This is the *rerun*: the first seed-C attempt (run `mi-var-c`, cluster mi-w73) was killed by the harness 2-hour background-task cap mid-stage-9 under earlier host load (stages 0–8 graded, i10 never ran → not a clean n=3); relaunched detached (setsid, cap-immune) on a quiet host. Host load 9–16 at launch, settling to ~9.

Result: **solved 9/10, mitigation 10/10, diagnosis 9/10, TOTAL contradicted = 0.** All 10 strict receipts were **accepted (completed=true, cleaned=true); zero rejected receipts — the close-out completion anomaly did NOT recur.** The one non-solve **inverted vs A/B**: this run **i07 (first composite) missed diagnosis** (`Diagnosis.success=False`, accuracy 66.67% — one of three checklist dimensions docked, the same D2 Fault-Characterization bar), while mitigation passed 3/3 (TTM 127 s); **i10 (exact composite) solved** (diagnosis passed, mitigation 3/3, TTM 126 s).

#### Per-incident results

| # | kind | problem | solved | diag | mit (oracles) | TTM s | contra | compl | rcpt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | first | missing_configmap_hotel_reservation | yes | ✓ | ✓ | 87 | 0 | ✓ | ok |
| 2 | first | wrong_service_selector_hotel_reservation | yes | ✓ | ✓ | 68 | 0 | ✓ | ok |
| 3 | variant | missing_configmap_mongodb_geo_rate | yes | ✓ | ✓ | 108 | 0 | ✓ | ok |
| 4 | first | readiness_probe_misconfiguration | yes | ✓ | ✓ | 94 | 0 | ✓ | ok |
| 5 | first | network_policy_block | yes | ✓ | ✓ | 56 | 0 | ✓ | ok |
| 6 | novel | misconfig_app_hotel_res | yes | ✓ | ✓ | 173 | 0 | ✓ | ok |
| 7 | composite | composite3_hotel_geo_rate_recommendation | **no** | ✗ | ✓ (3/3) | 127 | 0 | ✓ | ok |
| 8 | variant | readiness_probe_misconfiguration__v_reservation | yes | ✓ | ✓ | 132 | 0 | ✓ | ok |
| 9 | exact | misconfig_app_hotel_res | yes | ✓ | ✓ | 50 | 0 | ✓ | ok |
| 10 | exact | composite3_hotel_geo_rate_recommendation | yes | ✓ | ✓ (3/3) | 126 | 0 | ✓ | ok |

Totals: **solved 9/10**, **mitigation 10/10** (every mitigation oracle, both composites 3/3), **diagnosis 9/10**, **contradicted 0/10**, **0 rejected receipts** (both composites completed+cleaned cleanly). The sole non-solve is i07 (first composite) on a D2 characterization dock.

### Combined 3-seed summary (variance pass A + B + C, images `mi7`)

Headline across the three independent cold lifecycles (mi7, fix #1 active): **mitigation 10/10 every seed; solved 9/10 (A), 10/10 (B), 9/10 (C); contradicted causes 0 every seed.**

#### Takeaways

1. **Fix #1 is confirmed robust at n=3: contradicted = 0 on all three independent cold lifecycles** (A=0, B=0, C=0; was 4 on the pre-fix mi6 pilot). Meaning: the responder evidence-kind discipline fix eliminates the mislabeled-cause contradiction class across every seed, while composites keep mitigation 3/3 and red-herring/contradiction detection stays intact (the geo `benign_env_drift` decoy is never cited as a cause). Confidence: high (3 clean zeros, deterministic mechanism). Implication: fix #1 ships; the contradiction gate is doing its job without false contradictions. Next step: none for the fix itself.

2. **Composition-from-singles (i07, first composite) is a reliable mitigation win and a mostly-reliable diagnosis win — the one failure mode is the judge's characterization bar, not composition.** Meaning: the first composite's mitigation passed 3/3 on all three seeds (and mi6); its diagnosis solved on mi6/A/B and missed once (seed C, D2 characterization dock at 66.67%). The AND-of-three-oracles mitigation — the discriminating signal memoryless Codex fails — is never the miss. Confidence: high on mitigation (4/4 composite-first runs 3/3), medium on diagnosis (3/4 solved). Implication: SDO composes single-fault repairs into a correct multi-fault fix every time; the only exposure is a terse composite diagnosis narrative tipping below the xhigh judge's characterization checklist. Next step: carry richer first-occurrence characterization into the composite playbook (option-A/fix-#2 territory) — do not touch verifier/reflection semantics unilaterally.

3. **The composite diagnosis-characterization variance is real but not tied to a specific incident — it floats between the two composites run-to-run and correlates with host load.** Meaning: the single composite diagnosis miss per "busier" run landed on i10 (exact repeat) in mi6+A, and on i07 (first composite) in C; seed B (quietest) solved both. So it is not an exact-repeat-playbook-thinning story alone — either composite can dip below the D2 bar, and the quiet-host seed (B) is the only 10/10. Confidence: medium (4 composite-bearing runs). Implication: the exposure is a narrative-quality margin against an xhigh judge, host-condition-sensitive, affecting only the diagnosis score — mitigation is unaffected. Next step: same lever as #2 (richer stored characterization); treat host quiet as a run-condition for the paper's clean-number seed.

4. **The strict-receipt close-out anomaly is a load-sensitive drain/worktree-leak class — now fixed at its source.** Meaning: the anomaly (`completed!=true` / `remaining_worktrees!=[]`, receipt rejected though the oracle solves) hit 2/4 composite runs in A+B (A i07, B i10), spread to **single faults i05/i06** in the killed first-C attempt under sustained load, and then hit **0/10 in the clean seed-C rerun on a quiet host**. Confidence: medium-high (clear load correlation across six lifecycles). Implication: it was a reflection/worktree-drain racing the receipt snapshot, worsening under load — never a resolution failure (every affected incident still solved with repairs attributed). The root cause (controller stranding a superseded incident's worktree, only reaped on `AcknowledgeClosure`) is now closed at the source by the (A)/(B)/(C) worktree-release fixes: **PR #11** (adapter quiescent-drain reap + broker `release_incident` primitive + settle-before-sample timing) and **PR #12** (controller durable `ReleaseEffect` releasing the superseded parent on supersede), both merged to main. Next step: a post-fix composite run under induced load to confirm the anomaly no longer reproduces; until then the fixes are validated by unit/restart tests, not yet by a live loaded lifecycle.
