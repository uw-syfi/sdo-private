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
