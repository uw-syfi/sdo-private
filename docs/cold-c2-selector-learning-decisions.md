# Cold-C2 control and selector learning: decisions and results

Status: 2026-10-01, in progress (this file is updated as sequences finish). Branch `vic/exp/cold-c2-selector-learning` (from `vic/exp/simultaneous-composites`). Predecessor: `docs/simultaneous-composites-decisions.md`.

## Questions

1. Is the C2 token saving seen after C1 (0.54 to 0.72M versus 1.35 to 1.64M sequential) a memory effect? Control: run C2 (composite3b) FIRST on a fresh controller and `.sdo` (seeded identically to C1 of the earlier sequences), n>=4, and compare tokens and inj->mit to the stored `sim-a..d` C2 rows.
2. Does a selector detector, created at the end of the C3 reflection, fire before dispatch on a repeated C3, and does the repeat resolve `wrong_selector:frontend` faster? Sequence: C3 (composite5), C3' (composite5 again), then C1 (composite3), n>=3. Also record any later learned detector that false-fires on the healthy application and classify those runs.

## Decisions

- Same settings as the simultaneous arm: `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, images `nf4` (no rebuild), Codex gpt-6-luna, fast loop, probe-graded. Invocation reused verbatim from `seq_sim.sh` (copy `seq_sim2.sh`, only the worktree path differs) and the aggregator from `aggregate_sim.py` (copy `aggregate_sim2.py`, extra sequence names, output `agg_cmp2.json`).
- Worktree `/mnt/data/shli/sdo-worktrees/simul2`; `third_party/sregym` is a copy of the simul checkout (the composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed).
- Sequences are named `cc2-{a..d}` (cold C2, one composite each; the run id suffix is `-C1`) and `sel-{a..c}` (run ids `-C1` = C3, `-C2` = C3 repeat, `-C3` = C1). Clusters `cl-w90`+ (checked against `kind get clusters`), deleted at the end.
- Codex and sequential arms are not rerun; comparisons use the stored `sim-a..d` and `pf-a/b` rows.
- Load policy: wait up to 30 min if `uptime` load > 20; timeouts and install failures are classified as infra and listed separately.

## Design check: same flags as the simultaneous arm

Cold-C2 and selector sequences are launched by `seq_sim2.sh`, which differs from the original `seq_sim.sh` only in the worktree path (`diff` is empty after substituting the path): `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, images `nf4`, Codex gpt-6-luna, same seed (`lifecycle-stream`), same controller install path. The only intended difference for cold C2 is that composite3b is the first composite (fresh controller and `.sdo`, memory 0/1). Tokens are `responder_tokens + reflection_tokens` as in the stored rows.

## Design problem found in the selector repeat, and the fixes tried

In the first C3 the responder repaired `wrong_selector:frontend` by adding `current_service_name: frontend` to the frontend Deployment pod template and committed that to the application source (`kubernetes/frontend/frontend-deployment.yaml`). `up --redeploy` redeploys from that source, so on the repeat the injected selector (which adds the same label requirement) still matches the pods, and the fault is a no-op: `ever_red` is false and the probe is green at t=0.3 s (sel-a, sel-b, sel-c, sel2-a, sel2-c). The repeat therefore measures a persisted source fix, not detector learning.

- Attempt 1 (`seq_sim3.sh`, sequences `sel2-*`): commit a revert of `kubernetes/` to the baseline snapshot before each redeploy. This did not work: the harness commit never appeared in the workspace history (the operational repository is controller-managed and the commit was not kept), so the label stayed. sel2-a and sel2-c are therefore effectively more no-reset replicates.
- Attempt 2 (`seq_sim4.sh`, sequences `sel3-*`): after `up --redeploy` and before the run, remove the label from the live frontend Deployment (JSON patch, wait for rollout), leaving source and `.sdo` memory as the agent left them. This restores a live app in which the selector fault is real again while memory persists. The patch is logged in each sequence's `seq.log` ("removed frontend pod label before C2").

## Results (pending sequences are appended below)

<!-- RESULTS -->
### Experiment 1: cold C2 versus C2 after C1

Per run (all rows below; `never red` lists faults that were never red in the probe, `-` means every fault was red):

| seq | composite | solved | oracle | last-fault s | inj->mit s | tokens | per-fault s | never red | learned detectors fired after injection start (xN fingerprints, relation) | note |
|---|---|---|---|---|---|---|---|---|---|---|
| cc2-a | C2 (cold) | 3/3 | True | 193 | 237 | 2.04M | mongodb-geo 130, recommendation 73, profile 193 | - | none |  |
| cc2-b | C2 (cold) | 3/3 | True | 135 | 209 | 1.95M | mongodb-geo 135, recommendation 78, profile 78 | - | none |  |
| cc2-c | C2 (cold) | 3/3 | True | 131 | 177 | 1.86M | mongodb-geo 131, recommendation 73, profile 73 | - | none |  |
| sel-a | C3 | 5/5 | True | 139 | 159 | 1.75M | mongodb-rate 101, recommendation 58, geo 64, user 64, frontend 139 | - | none |  |
| sel-a | C3' (repeat) | 5/5 | True | 48 | 74 | 0.76M | mongodb-rate 48, recommendation 48, geo 48, user 48, frontend 0 | frontend | deny-all-application-network-policyx1(before/pre) |  |
| sel-a | C1 (after C3,C3') | 3/3 | True | 364 | 261 | 1.16M | mongodb-rate 128, recommendation 107, geo 364 | - | deny-all-application-network-policyx1(before/pre) |  |
| sel-b | C3 | 5/5 | True | 149 | 177 | 1.57M | mongodb-rate 122, recommendation 80, geo 90, user 80, frontend 149 | - | none |  |
| sel-b | C3' (repeat) | 5/5 | True | 70 | 105 | 0.31M | mongodb-rate 64, recommendation 64, geo 70, user 64, frontend 0 | frontend | required-configmap-missingx1(before/pre) |  |
| sel-b | C1 (after C3,C3') | 3/3 | False | 874 | 672 | 0.00M | mongodb-rate 523, recommendation 874, geo 865 | - | required-configmap-missingx1(before/pre) | 1 incident receipt(s) missing |
| sel-c | C3 | 5/5 | True | 285 | 339 | 3.01M | mongodb-rate 285, recommendation 91, geo 285, user 96, frontend 219 | - | none |  |
| sel-c | C3' (repeat) | 5/5 | True | 1077 | 648 | 2.33M | mongodb-rate 88, recommendation 1077, geo 995, user 1051, frontend 989 | - | network-policy-deny-allx1(before/pre), required-configmap-missingx1(before/pre) |  |
| sel-c | C1 (after C3,C3') | 3/3 | True | 121 | None | 0.27M | mongodb-rate 121, recommendation 111, geo 116 | - | network-policy-deny-allx1(before/pre), required-configmap-missingx1(before/pre) |  |
| sel2-a | C3 | 5/5 | True | 118 | 104 | 1.56M | mongodb-rate 118, recommendation 107, geo 102, user 91, frontend 107 | - | none |  |
| sel2-a | C3' (repeat) | 5/5 | True | 112 | 106 | 0.40M | mongodb-rate 43, recommendation 101, geo 112, user 101, frontend 0 | frontend | required-configmap-missingx1(before/pre) |  |
| sel2-a | C1 (after C3,C3') | 3/3 | True | 74 | 60 | 0.22M | mongodb-rate 47, recommendation 47, geo 74 | - | required-configmap-missingx1(before/pre) |  |
| sel2-c | C3 | 5/5 | True | 177 | 230 | 1.92M | mongodb-rate 117, recommendation 133, geo 96, user 96, frontend 177 | - | none |  |
| sel2-c | C3' (repeat) | 5/5 | True | 75 | 50 | 0.45M | mongodb-rate 69, recommendation 69, geo 69, user 75, frontend 0 | frontend | deny-all-network-policyx1(before/pre), missing-configmapx1(before/pre) |  |
| sel2-c | C1 (after C3,C3') | 3/3 | True | 99 | 109 | 0.31M | mongodb-rate 93, recommendation 93, geo 99 | - | deny-all-network-policyx1(before/pre), missing-configmapx1(before/pre) |  |
| sel3-a | C3 | 5/5 | False | 557 | None | 0.20M | mongodb-rate 355, recommendation 557, geo 551, user 102, frontend 557 | - | none |  |
| sel3-a | C3' (repeat) | 5/5 | True | 517 | 518 | 1.27M | mongodb-rate 517, recommendation 280, geo 425, user 332, frontend 102 | - | none |  |

Cold C2 (composite3b first, memory 0/1) versus the stored warm C2 (after C1, `sim-a..d`):

| metric | cold C2 (cc2-a, b, c; d pending) | warm C2 after C1 (sim-a..d) |
|---|---|---|
| tokens | 2.04M, 1.95M, 1.86M (mean 1.95M) | 0.66M, 0.55M, 0.72M, 0.54M (mean 0.62M) |
| inj->mit s | 237, 209, 177 (median 209) | n/a, 104, 161, 79 (median 104) |
| last-fault s | 193, 135, 131 (median 135) | 109, 73, 152, 62 (median 91) |
| solved / oracle | 3/3, 3/3 | 4/4 / 4/4 |
| learned detectors before dispatch | none (nothing learned yet) | geo-free ConfigMap and netpol detectors fired before dispatch in 3 of 4 (see the simultaneous doc) |

The cold C2 token cost (1.9 to 2.0M) matches the cold C1 simultaneous runs (1.36 to 3.19M, median about 1.96M) and is about 3x the warm C2; every cold C2 is above every warm C2 (no overlap). Speed: inj->mit is about 2x slower cold (209 versus 104 s median; ranges 177 to 237 versus 79 to 161), last-fault about 1.5x slower, but the ranges overlap on last-fault and warm C2 had a 161 s outlier, so speed is suggestive only.

### Experiment 2: selector learning

Sequences C3, C3' (repeat), C1. Valid no-reset sequences: sel-a, sel-b, sel-c, sel2-a, sel2-c (the repeat's `wrong_selector` was a no-op, see the design problem). Label-reset sequences: sel3-a (done), sel3-b and sel3-c (running). Infrastructure failures are kept as `*-infra*` directories and are not in the table.

- Selector detector created after the first C3 reflection: sel2-c only (`service_selector_mismatch`, plus `empty_network_policy` and `missing_configmap`). sel-a, sel-b, sel-c, sel2-a and sel3-a created none (they learned network-policy and ConfigMap detectors, sel-c also `persistent_wrk2_load`; sel3-a learned nothing because its first responder produced no diagnosis). Pooled with the earlier sim-a to sim-d, a selector detector was created in 3 of 9 first-C3 reflections (sim-a, sim-b, sel2-c).
- Did the selector detector fire before dispatch on the repeat? In sel2-c, no: the repeat's selector fault was a no-op, and the detector did not fire (no healthy-service false fire either). That is uninformative about firing on a real fault. sel3 (label reset) is the valid test and only counts if the detector exists.
- False fires of later detectors on healthy services: none in the sel-a, sel-b, sel-c, sel2-a, sel2-c repeat and C1 runs (each learned incident detector fired on one fingerprint, the injected fault, before dispatch). The sim-a C4 failure mode (selector detector firing on four healthy services after rollout) did not recur.
- Per-fault times for the first C3 (cold, 5 faults): wrong_selector 139, 149, 219, 107, 177 s (sel-a, b, c, sel2-a, sel2-c); repeat: 0 s (no-op), so no valid speed comparison; the other four faults resolved in 43 to 112 s on the repeat except sel-c (below).
- Classification of runs: sel-b C1 (composite3 after the repeats): probes resolved at 874 s, oracle false, responder tokens 0, receipt missing, learned configmap detector fired before dispatch; anomalous (no responder token record, 672 s inj->mit); treated as a failed run, possible infra (load/Codex) but not proven. sel-c repeat: 1077 s last fault, 2.33M tokens, controller load (host load 20 to 45 then); slow-run outlier, learned detectors fired before dispatch but did not shorten it. sel3-a C1 (first C3): oracle false, responder 0.20M tokens, empty diagnosis, probes green at 557 s; anomalous cold run, kept in the table, nothing learned. Infra failures excluded: sel-c (first attempt), cc2-c (first), cc2-d (twice), sel2-b, sel3-b, sel3-c (first attempts): kind/etcd i/o timeouts, Calico apply failure, ControllerInstallError.

## Takeaways (draft; final after all sequences)

1. Meaning: the C2 token saving is a memory effect, not a property of composite3b. Cold C2 costs about the same as cold C1 (about 1.9M) and the warm C2 costs about 0.6M (3x less, no overlap, n=3 vs 4). Confidence: medium (n=3 cold, direction consistent, magnitude large, but one benchmark composite and one model). Implication: learned detectors and playbooks from an earlier composite cut the second composite's responder cost; the simultaneous arm's earlier "cost effect" claim stands. Next step: a cold-C1 control against C1-after-C2 would confirm symmetry; none required.
2. Meaning: speed is also better warm, but weakly: inj->mit 104 versus 209 s median, ranges overlapping on last-fault. Confidence: low to medium. Implication: do not report a speed learning curve from n<=4 per cell; report the token effect. Next step: n>=8 if a speed claim is wanted.
3. Meaning: the selector repeat was confounded by a persistent source fix; the experiment as specified (C3, C3, C1) cannot show selector learning unless the live app is reset. Confidence: high (direct evidence: ever_red false and t=0.3 s in five repeats, label present in source). Implication: any repeat-of-a-fault design must reset application source or live state, not only keep `.sdo`. Next step: see sel3 results below.
4. Meaning: selector detectors are created rarely (3 of 9 first-C3 reflections), so even a valid repeat protocol has low power for detector firing. Confidence: medium. Implication: to test firing, seed a known selector detector or run more first-C3 reflections. Next step: seed the sel2-c detector into a fresh memory and run C3 with the label reset (cheap).

## Caveats

- n=3 (cold C2; d pending), five no-reset and (pending) three label-reset selector sequences; host load 8 to 45 shared with another user; several sequences hit infra failures and were rerun on new clusters (cl-w100 and above), so concurrency and load differ between waves.
- Tokens are responder plus reflection tokens; cold rows exclude judge time. The warm C2 baseline is the stored `sim-a..d`, not rerun, from a different day.
- `inj->mit` is n/a where no closure receipt was recorded.
- The label-reset patch alters live state only; source keeps the agent's earlier fix.
