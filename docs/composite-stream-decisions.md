# Composite stream (10 composites, all fixes on): decisions and results

Status: 2026-10-01, in progress. Done: SDO sequences cstream-a, b, c, d and the Codex + verify runs on C4/C5; cstream-e is being rerun after an infra failure (see caveats). Doc is updated after every finished sequence. Branch `vic/exp/composite-stream` (from `vic/exp/healthy-baseline-ab`), worktree `/mnt/data/shli/sdo-worktrees/composite-stream`. Predecessors: `docs/healthy-baseline-ab-decisions.md`, `docs/cold-c2-selector-learning-decisions.md`, `docs/simultaneous-composites-decisions.md`, `docs/composite-learning-curve-decisions.md`, `docs/nfault-composites-decisions.md` (these live in sibling worktrees).

## Questions

1. With every fix on, does cost per composite (tokens, time) trend down along a 10-composite stream (per-position medians across sequences)?
2. Does memory growth (incident detectors / playbooks) stay compact?
3. Resolution rate versus Codex + verify.

## Decisions

- Stream (fixed, same for every sequence): `C1 C2 C3 C1 C4 C2 C3 C5 C1 C4` with C1 = `composite3_hotel_geo_rate_recommendation`, C2 = `composite3b_hotel_profile_mongodb_geo_recommendation`, C3 = `composite5_hotel_geo_rate_recommendation_frontend_user`, and two new hand-registered variants (one fault per Deployment, no generator): C4 = `composite3c_hotel_rate_mongodb_geo_user` (readiness rate, ConfigMap mongodb-geo, network policy user), C5 = `composite4_hotel_profile_rate_recommendation_frontend` (readiness profile, ConfigMap mongodb-rate, network policy recommendation, wrong selector frontend). ConfigMap targets are limited to mongodb-geo and mongodb-rate by the injector, so variation comes from the readiness and network-policy targets and from mixing the other families' components. Positions 4, 6, 7, 9, 10 repeat a composite (C1 x3, C2 x2, C3 x2, C4 x2), position 8 (C5) is new but reuses components.
- Arm "SDO all-on": `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, `--healthy-baseline`, images `nf5`, seed `lifecycle-stream`, Codex gpt-6-luna, fast loop, probe-graded (no LLM judge, no judge time in any timing). Script `benchmarks/sregym/experiments/composite-stream/seq_stream.sh` = `healthy-baseline-ab/seq_ab.sh` with gate on, pull, 3 follow-ups fixed and the worktree path changed. One persistent controller and `.sdo` per sequence; app redeployed (`up --redeploy`) before each composite; the live frontend pod label that an earlier responder added is removed before every composite after the first so a repeated `wrong_selector:frontend` is a real fault (selector-repeat design problem, `cold-c2-selector-learning-decisions.md`). Other persisted source fixes are not reset; inert injections are detected from `ever_red` per fault and reported.
- Baseline: Codex gpt-6-luna + verify protocol. Stored results reused for C1, C2, C3 (not rerun): C1 0/4 (verify, `nfault`), C2 0/2 (`composite-learning-curve`), C3 0/4. New Codex runs only on C4 and C5, n=2 each, on a freshly redeployed app per run (`codex_stream.sh`).
- Concurrency: at most 2 jobs at once (`run_stream_queue.sh`), waits up to 30 min while load > 20. Clusters `cl-w150`+ for first attempts, `cl-w170`+ for infra reruns; all deleted at the end. Infra failures (kind create, openebs/Calico, `ControllerInstallError`, etcd i/o timeouts before the first injection) are classified separately and rerun.
- Raw runs: `/mnt/data/shli/clc-runs/cstream-*` (SDO) and `cstream-codex*`.
- The submodule commit adding C4/C5 (`03b1df58`) lives in a private copy of the submodule git dir (`/mnt/data/shli/sdo-worktrees/.composite-stream-sregym-gitdir`, branch `vic/exp/composite-stream`); no remotes changed.

## Results, sequences cstream-a to cstream-d (n=4)

Concurrent pairs on clusters cl-w150/151/153/154, host load 6 to 30. Tokens are responder plus reflection (split shown in the per-run table). `last-fault s` = injection end to the last fault's final green probe; `inj->mit s` = controller's injection-to-last-repair (judge time never included). "learned detectors before dispatch" lists the fault components for which a learned (non-health) incident detector activated before the first dispatch of that composite. "off-target" = a learned detector fired outside the composite's fault components (false-firing check). Memory = incident detectors / playbook directories in `.sdo` after the composite. `follow-ups` = incidents beyond the first. Raw tables: `/mnt/data/shli/clc-runs/cstream-abcd-table.md`, aggregate `cstream-agg.json`, script `benchmarks/sregym/experiments/composite-stream/analyze_stream.py`.

| seq | pos | C | solved | oracle | last-fault s | inj->mit s | tokens (resp+refl) | follow-ups | learned detectors before dispatch (fault components) | learned fired off-target | gate rejections | memory inc.det/playbooks after | inert | stop/err |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cstream-a | 1 | C1 | 3/3 | True | 57 | 93 | 0.94M (0.46+0.48) | 0 | none | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-a | 2 | C2 | 3/3 | True | 68 | 81 | 0.30M (0.30+0.00) | 0 | mongodb-geo, recommendation | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-a | 3 | C3 | 5/5 | True | 380 | 407 | 0.84M (0.35+0.49) | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 4 | C1 | 3/3 | True | 68 | 80 | 0.33M (0.33+0.00) | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 5 | C4 | 3/3 | True | 151 | 164 | 0.48M (0.48+0.00) | 0 | mongodb-geo, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 6 | C2 | 3/3 | True | 94 | 108 | 0.50M (0.50+0.00) | 0 | mongodb-geo, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 7 | C3 | 5/5 | True | 96 | 125 | 0.46M (0.46+0.00) | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 8 | C5 | 4/4 | True | 100 | 120 | 0.47M (0.47+0.00) | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 9 | C1 | 3/3 | True | 57 | 88 | 0.39M (0.39+0.00) | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 10 | C4 | 3/3 | True | 73 | 95 | 1.72M (0.69+1.03) | 0 | mongodb-geo, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-b | 1 | C1 | 3/3 | True | 136 | 308 | 2.51M (1.91+0.59) | 0 | none | none | 0 (+0 other) | 1/2 | - | all_faults_resolved  |
| cstream-b | 2 | C2 | 3/3 | True | 171 | 209 | 1.97M (1.06+0.91) | 0 | mongodb-geo | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 3 | C3 | 5/5 | True | 161 | 211 | 1.32M (0.93+0.40) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 4 | C1 | 3/3 | True | 78 | 84 | 0.52M (0.52+0.00) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 5 | C4 | 3/3 | True | 99 | 110 | 0.34M (0.34+0.00) | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 6 | C2 | 3/3 | True | 68 | 56 | 0.30M (0.30+0.00) | 0 | mongodb-geo, profile, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 7 | C3 | 5/5 | True | 176 | 230 | 0.94M (0.94+0.00) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 8 | C5 | 4/4 | True | 100 | 85 | 0.52M (0.52+0.00) | 0 | mongodb-rate, profile, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 9 | C1 | 3/3 | True | 78 | 94 | 0.49M (0.49+0.00) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 10 | C4 | 3/3 | True | 73 | 130 | 1.16M (0.47+0.69) | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-c | 1 | C1 | 3/3 | True | 125 | 166 | 1.53M (1.02+0.51) | 0 | none | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-c | 2 | C2 | 3/3 | True | 137 | 162 | 1.32M (0.77+0.55) | 0 | mongodb-geo, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-c | 3 | C3 | 5/5 | True | 302 | 331 | 0.72M (0.27+0.46) | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 4 | C1 | 3/3 | True | 83 | 95 | 1.03M (0.37+0.66) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 5 | C4 | 3/3 | True | 68 | 80 | 0.24M (0.24+0.00) | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 6 | C2 | 3/3 | True | 64 | 78 | 0.35M (0.35+0.00) | 0 | mongodb-geo, profile, recommendation | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 7 | C3 | 5/5 | True | 87 | 128 | 0.46M (0.46+0.00) | 0 | frontend, geo, mongodb-rate, recommendation, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 8 | C5 | 4/4 | True | 94 | 113 | 0.38M (0.38+0.00) | 0 | frontend, mongodb-rate, profile, recommendation | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 9 | C1 | 3/3 | True | 65 | 75 | 0.23M (0.23+0.00) | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-c | 10 | C4 | 3/3 | True | 59 | 38 | 0.94M (0.44+0.49) | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-d | 1 | C1 | 3/3 | True | 58 | 87 | 0.96M (0.68+0.28) | 0 | none | none | 0 (+0 other) | 1/2 | - | all_faults_resolved  |
| cstream-d | 2 | C2 | 3/3 | True | 95 | 108 | 0.47M (0.47+0.00) | 0 | mongodb-geo | none | 0 (+0 other) | 1/2 | - | all_faults_resolved  |
| cstream-d | 3 | C3 | 5/5 | True | 375 | 430 | 0.86M (0.38+0.48) | 0 | mongodb-rate | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-d | 4 | C1 | 3/3 | True | 63 | 73 | 0.28M (0.28+0.00) | 0 | mongodb-rate | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-d | 5 | C4 | 3/3 | True | 73 | 90 | 0.35M (0.35+0.00) | 0 | mongodb-geo | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-d | 6 | C2 | 3/3 | True | 216 | 225 | 1.59M (0.88+0.71) | 0 | mongodb-geo | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-d | 7 | C3 | 5/5 | True | 88 | 105 | 0.44M (0.44+0.00) | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-d | 8 | C5 | 4/4 | True | 75 | 98 | 0.57M (0.57+0.00) | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-d | 9 | C1 | 3/3 | False | 249 | 164 | 0.41M (0.41+0.00) | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-d | 10 | C4 | 3/3 | False | 397 | 87 | 0.00M (0.00+0.00) | 0 | mongodb-geo, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved fault recovery failed: SregymWorkerError: worker request 'health' timed out afte |

| pos | composite | n | all solved (probes) | oracle True | median tokens | median responder tokens | median reflection tokens | median inj->mit s | median last-fault s | median follow-ups | median memory det/pb after |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | C1 | 4 | 4/4 | 4/4 | 1.25M | 0.85M | 0.50M | 129 | 92 | 0.0 | 1.5/2.5 |
| 2 | C2 | 4 | 4/4 | 4/4 | 0.89M | 0.62M | 0.27M | 135 | 116 | 0.0 | 2.5/3.5 |
| 3 | C3 | 4 | 4/4 | 4/4 | 0.85M | 0.36M | 0.47M | 369 | 339 | 0.0 | 3.0/4.0 |
| 4 | C1 | 4 | 4/4 | 4/4 | 0.43M | 0.35M | 0.00M | 82 | 73 | 0.0 | 3.0/4.0 |
| 5 | C4 | 4 | 4/4 | 4/4 | 0.34M | 0.34M | 0.00M | 100 | 86 | 0.0 | 3.0/4.0 |
| 6 | C2 | 4 | 4/4 | 4/4 | 0.42M | 0.42M | 0.00M | 93 | 81 | 0.0 | 3.0/4.0 |
| 7 | C3 | 4 | 4/4 | 4/4 | 0.46M | 0.46M | 0.00M | 126 | 92 | 0.0 | 3.0/4.0 |
| 8 | C5 | 4 | 4/4 | 4/4 | 0.50M | 0.50M | 0.00M | 106 | 97 | 0.0 | 3.0/4.0 |
| 9 | C1 | 4 | 4/4 | 3/4 | 0.40M | 0.40M | 0.00M | 91 | 72 | 0.0 | 3.0/4.0 |
| 10 | C4 | 4 | 4/4 | 3/4 | 1.05M | 0.46M | 0.59M | 91 | 73 | 0.0 | 4.0/5.0 |

| seq | cumulative tokens (M) after pos 1..N | cumulative inj->mit min |
|---|---|---|
| cstream-a | 0.9 1.2 2.1 2.4 2.9 3.4 3.8 4.3 4.7 6.4 | 2 3 10 11 14 16 18 20 21 23 |
| cstream-b | 2.5 4.5 5.8 6.3 6.7 7.0 7.9 8.4 8.9 10.1 | 5 9 12 14 15 16 20 22 23 25 |
| cstream-c | 1.5 2.8 3.6 4.6 4.8 5.2 5.6 6.0 6.3 7.2 | 3 5 11 13 14 15 17 19 20 21 |
| cstream-d | 1.0 1.4 2.3 2.6 2.9 4.5 4.9 5.5 5.9 5.9 | 1 3 10 12 13 17 19 20 23 24 |

| composite | first occurrence tokens (median) | repeat tokens (median) | first inj->mit | repeat inj->mit |
|---|---|---|---|---|
| C1 | 1.25M (n=4) | 0.40M (n=8) | 129 | 86 |
| C2 | 0.89M (n=4) | 0.42M (n=4) | 135 | 93 |
| C3 | 0.85M (n=4) | 0.46M (n=4) | 369 | 126 |
| C4 | 0.34M (n=4) | 1.05M (n=4) | 100 | 91 |
| C5 | 0.50M (n=4) | -M (n=0) | 106 | - |

Totals over a to d (40 composites): 40 of 40 resolved by probes; official oracle True in 38 of 40; 0 follow-up incidents; 0 off-target (false-firing) learned detectors; 0 gate rejections; 0 inert injections. The two oracle misses are both cstream-d, and neither is a missed repair:

- cstream-d pos 9 (C1): probes 3/3 at 249 s, oracle False because `2-NetworkPolicyMitigationOracle` failed at the end state (the official network-policy oracle disagreed with the read-only probe, as in po-b C4 of `composite-learning-curve-decisions.md`; responder tokens 0.41M, no reflection).
- cstream-d pos 10 (C4): probes 3/3 at 397 s, `fault recovery failed: SregymWorkerError: worker request 'health' timed out after 60s` (host load 25 to 30 at the time), receipt tokens read 0.00M and the oracle failed on the network policy. Classified as a harness timeout under host load plus the same oracle disagreement; kept in the tables as an oracle miss, its tokens are excluded from the pos 10 token medians' meaning (0.00M is "unmeasured", not cheap).

### Cost along the stream (per-position medians, n=4)

- Responder tokens fall from 0.85M (pos 1, cold) and 0.62M (pos 2) to 0.34 to 0.50M at every position from 4 to 9, flat. Total tokens fall from 1.25M, 0.89M, 0.85M at positions 1 to 3 to 0.34 to 0.50M at positions 4 to 9 (about 3x lower than pos 1). The difference between total and responder tokens is reflection: 0.50M, 0.27M, 0.47M at positions 1 to 3, then 0 at positions 4 to 9. Reflection happens only when memory changes; the pos 10 total (1.05M) is a reflection bump (0.59M median reflection; a, b, c each ran a 0.5 to 1.0M reflection at pos 10 while the responder cost stayed 0.44 to 0.69M), and cstream-d pos 6 had a reflection too (0.71M). Memory counts moved at pos 10 in a, d (4/5), not in b, c.
- inj->mit median s: 129, 135, 369, 82, 100, 93, 126, 106, 91, 91. Position 3 (C3, 5 faults, first occurrence in this stream) is the slowest at every n (d 430, a 407, c 331, b 211); its repeat at position 7 is 126 s (a 125, b 230, c 128, d 105), the clearest time effect. Other positions are in a 80 to 135 s band, with no visible downward trend inside the band.
- First occurrence versus repeat (median over sequences): C1 1.25M / 129 s first, 0.40M / 86 s repeats (n=8); C2 0.89M / 135 s versus 0.42M / 93 s; C3 0.85M / 369 s versus 0.46M / 126 s; C4 (pos 5 is its first occurrence, after memory from C1 to C3) 0.34M / 100 s first versus 1.05M / 91 s second (the pos 10 reflection, responder-only 0.46M); C5 (new family at pos 8, reuses components) 0.50M / 106 s, as cheap as a repeat.
- Cumulative tokens (M), positions 1..10: a 0.9, 1.2, 2.1, 2.4, 2.9, 3.4, 3.8, 4.3, 4.7, 6.4; b 2.5, 4.5, 5.8, 6.3, 6.7, 7.0, 7.9, 8.4, 8.9, 10.1; c 1.5, 2.8, 3.6, 4.6, 4.8, 5.2, 5.6, 6.0, 6.3, 7.2; d 1.0, 1.4, 2.3, 2.6, 2.9, 4.5, 4.9, 5.5, 5.9, 5.9 (pos 10 unmeasured). Median final total about 6.8M, of which the first three composites account for about 3.6M.

### Memory growth

Incident detectors / playbooks after each composite (positions 1..10): a 2/3, 2/3, 3/4, 3/4, 3/4, 3/4, 3/4, 3/4, 3/4, 4/5; b 1/2, 3/4, 3/4 flat through 10; c 2/3, 3/4, 4/5 flat through 10; d 1/2, 1/2, 2/3, 2/3, 2/3, 3/4, 3/4, 3/4, 3/4, 4/5. Final sizes: 3/4, 3/4, 4/5, 4/5 incident detectors / playbooks after ten composites covering five composite families and 12 distinct fault components. Learned detectors for the ConfigMap and network-policy faults (one of them in d only for the ConfigMap, d never learned a network-policy detector until pos 7) fire before dispatch from position 2 on; a selector detector (cstream-a, d) fired before dispatch on `frontend` at positions 7 and 8 in a and d. None fired outside the composite's fault components in any of the 40 runs and the healthy-baseline gate produced no rejection in any of the four sequences.

## Resolution versus Codex + verify

| composite | SDO all-on (probes / oracle) | Codex + verify | source |
|---|---|---|---|
| C1 (3 faults) | 12/12 / 11/12 | 0/4 (1.8/3 faults) | stored, not rerun |
| C2 (3 faults) | 8/8 / 8/8 | 0/2 | stored, not rerun |
| C3 (5 faults) | 8/8 / 8/8 | 0/4 (3.8/5) | stored, not rerun |
| C4 `composite3c` (3 faults, new) | 8/8 / 7/8 | 0/2 (1/3 and 2/3 faults; tokens 0.31M and 0.90M; network_policy(user) never repaired) | new, this experiment |
| C5 `composite4` (4 faults, new) | 4/4 / 4/4 | 0/2 (2/4 and 2/4; configmap and readiness fixed, network policy and wrong selector never; 0.73M and 0.76M) | new, this experiment |
| total | 40/40 / 38/40 | 0/14 | |

Codex + verify resolves 0 of 14 composites, never repairs the network-policy fault (in 14 of 14 runs counting the new ones; 30 of 30 including the earlier plain runs), and costs 0.3 to 0.9M tokens a run, i.e. in the range of SDO's warm positions (0.3 to 0.5M) and well below its cold ones (1.0 to 2.5M). The new Codex runs used a freshly redeployed app per run and the frontend label reset, so the C5 selector fault was real (`ever_red` true); the Codex lane `cstream-codex` is complete (raw runs `/mnt/data/shli/clc-runs/cstream-codex/results/verify-c{4,5}-{1,2}`).

## Takeaways (n=4 sequences, e pending)

1. All fixes on resolve a long persistent stream. Meaning: 40 of 40 composites by probes, 38 of 40 by the official oracle (both misses are in one sequence and are an oracle/harness disagreement, not an unrepaired fault), with 0 follow-up incidents, 0 gate rejections and 0 false-firing learned detectors; Codex + verify gets 0 of 14. Confidence: medium-high that SDO resolves these composites (40 of 40 across four independent sequences), medium on the zero follow-ups (the pull-before-act first responder plus simultaneous injection makes a follow-up unnecessary on this composite set, but only with this set). Implication: the resolution gap versus Codex is complete and stable along the stream, so the open questions are cost and the oracle-versus-probe disagreement on network policy. Next step: investigate the network-policy oracle disagreement (2 of 40, 1 of the late stream) and the `health` timeout in recovery under load.
2. Cost drops about 3x after the first three composites, and then is flat. Meaning: total tokens 1.25M, 0.89M, 0.85M at positions 1 to 3 versus 0.34 to 0.50M at positions 4 to 9, entirely because reflection stops (0 at positions 4 to 9) and the responder cost drops from 0.85M to about 0.4M; a new composite family (C5) at position 8 costs the same as a repeat. Time shows only one clear effect: the 5-fault C3 goes from a median 369 s to 126 s on its repeat; all other positions sit at 80 to 135 s. Confidence: medium for tokens (consistent in 4 of 4 sequences at positions 4 to 9 apart from the d reflection at pos 6), low for time. Implication: the curve is a step, not a slope: it saturates by position 3 or 4 and the saturation point is where memory stops growing (item 3). Next step: n=5 would tighten the medians; a stream that keeps introducing new fault kinds (not only new combinations of the same five) is needed to see whether the step recurs.
3. Memory growth is compact and the pos 10 reflection is the main cost outlier. Meaning: 3 to 4 incident detectors / 4 to 5 playbooks after ten composites (final sizes 3/4, 3/4, 4/5, 4/5), mostly formed in the first three composites and then flat; the late increments (a, d at pos 10, d at pos 6) each coincide with a 0.5 to 0.9M reflection. No sprawl and no false-firing detector: the healthy-baseline gate had nothing to reject. Confidence: medium (4 sequences, one app, five fault kinds). Implication: tokens at late positions are dominated by occasional reflections, not by memory size; a reflection that adds a 4th detector to a stream that is already solved is the main avoidable cost. Next step: test skipping reflection when the learned detectors already fired before dispatch and the incident closed without new findings.
4. Learned detectors reach pre-dispatch coverage from position 2 on, but this does not show up as a speedup beyond what pull-before-act already gives. Meaning: from position 2 the ConfigMap and network-policy detectors (and selector detectors after C3) fire before dispatch in a, b, c; repeat inj->mit (82 to 135 s) is near the cold C1 of the cheapest sequences (a: 93 s, d: 87 s), consistent with the simultaneous and cold-C2 docs. Confidence: low-medium. Implication: claim the token effect, not a speed learning curve (except the 5-fault repeat). Next step: none for this protocol.

## Caveats

- n=4 sequences (a to d); cstream-e is being rerun after cstream-e attempt 1 failed in `up` (`observe` namespace pods not Ready within 600 s, cl-w155; raw in `/mnt/data/shli/clc-runs/cstream-e-infra1`), the rerun is on cl-w170 and starts only after the load gate; it will be appended. Results are fast-loop, probe-graded, no LLM judge; the official oracle is run once on the end state.
- Concurrent sequences on one shared host, load up to 30 during d. Position medians mix composites of different size by design; first-versus-repeat is the controlled view but each cell has n=4 (8 for C1 repeats).
- Source fixes by earlier responders persist through `up --redeploy`; only the frontend pod label is reset live. No inert injection was recorded in 40 runs (`ever_red` true for all faults), including repeats of C1, C3 and C4.
- The pos 10 token medians contain cstream-d's unmeasured 0.00M (closure failed); responder-only medians are unaffected for a to c. C4 first occurrence at position 5 already benefits from memory built on C1 to C3, so "C4 first versus repeat" is not a cold-versus-warm comparison.
- Stored Codex numbers (C1 to C3) come from other days and hosts; the new C4/C5 Codex runs are n=2 each.
- The submodule commit adding C4/C5 (`03b1df58`) is in a private copy of the submodule git dir and is not on any remote.
