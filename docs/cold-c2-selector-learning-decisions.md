# Cold-C2 control and selector learning: decisions and results

Status: 2026-10-01, complete. All sequences finished; clusters cl-w90 to cl-w117 deleted. Branch `vic/exp/cold-c2-selector-learning` (from `vic/exp/simultaneous-composites`). Predecessor: `docs/simultaneous-composites-decisions.md`.

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

## Results

Scripts: `benchmarks/sregym/experiments/cold-c2-selector-learning/` (`seq_sim2.sh` plain, `seq_sim3.sh` manifest-reset attempt, `seq_sim4.sh` live label reset, `seq_sim5.sh` seeded selector memory, `aggregate_sim2.py`, `report2.py`). Raw runs: `/mnt/data/shli/clc-runs/{cc2,sel,sel2,sel3,selw}-*`. In the `sel*` rows the first row is C3, the second the repeat C3', the third C1.

### Per run

Columns: `never red` = faults never red in the probe (`-` = all were red); learned detectors = incident detectors that fired after the composite's injection start, xN = distinct fingerprints, with the relation to dispatch (`before/pre` = before the first dispatch).

| seq | composite | solved | oracle | last-fault s | inj->mit s | tokens | per-fault s | never red | learned detectors fired after injection start (xN fingerprints, relation) | note |
|---|---|---|---|---|---|---|---|---|---|---|
| cc2-a | C2 (cold) | 3/3 | True | 193 | 237 | 2.04M | mongodb-geo 130, recommendation 73, profile 193 | - | none |  |
| cc2-b | C2 (cold) | 3/3 | True | 135 | 209 | 1.95M | mongodb-geo 135, recommendation 78, profile 78 | - | none |  |
| cc2-c | C2 (cold) | 3/3 | True | 131 | 177 | 1.86M | mongodb-geo 131, recommendation 73, profile 73 | - | none |  |
| cc2-d | C2 (cold) | 3/3 | True | 983 | 443 | 2.90M | mongodb-geo 952, recommendation 983, profile 952 | - | none |  |
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
| sel3-a | C1 (after C3,C3') | 3/3 | True | 1282 | 304 | 4.21M | mongodb-rate 1244, recommendation 1282, geo 862 | - | none |  |
| sel3-b | C3 | 5/5 | True | 1311 | 1134 | 3.52M | mongodb-rate 118, recommendation 525, geo 1311, user 896, frontend 146 | - | none |  |
| sel3-b | C3' (repeat) | 4/5 | False | None | 357 | 1.04M | mongodb-rate 291, recommendation 648, user 730, frontend 314 | - | service-selector-ready-pod-mismatchx4(before/pre) |  |
| sel3-b | C1 (after C3,C3') | 3/3 | True | 94 | 132 | 1.68M | mongodb-rate 94, recommendation 68, geo 68 | - | service-selector-ready-pod-mismatchx6(before/pre) |  |
| sel3-c | C3 | 5/5 | False | 1766 | 279 | 3.51M | mongodb-rate 1447, recommendation 1766, geo 1537, user 608, frontend 247 | - | none |  |
| sel3-c | C3' (repeat) | 5/5 | False | 119 | 135 | 1.17M | mongodb-rate 119, recommendation 90, geo 85, user 85, frontend 85 | - | none |  |
| sel3-c | C1 (after C3,C3') | 2/3 | False | None | 61 | 0.21M | mongodb-rate 47, geo 47 | - | missing-required-configmapx1(before/pre) |  |
| selw-a | C3 | 5/5 | True | 878 | 833 | 0.90M | mongodb-rate 803, recommendation 878, geo 850, user 430, frontend 365 | - | deny-all-network-policyx1(before/pre), missing-configmapx1(before/pre), service-selector-mismatchx1(before/pre) |  |
| selw-b | C3 | 5/5 | True | 112 | 140 | 0.45M | mongodb-rate 59, recommendation 53, geo 112, user 112, frontend 64 | - | deny-all-network-policyx1(before/pre), missing-configmapx1(before/pre), service-selector-mismatchx1(before/pre) |  |
| selw-c | C3 | 5/5 | True | 69 | 95 | 0.57M | mongodb-rate 69, recommendation 64, geo 58, user 58, frontend 69 | - | deny-all-network-policyx1(before/pre), missing-configmapx1(before/pre), service-selector-mismatchx1(before/pre) |  |

Sequence kinds: `cc2-*` cold C2; `sel-*`, `sel2-*` C3, C3, C1 without any reset (repeat selector fault is a no-op); `sel3-*` same with the live frontend label removed before the repeat (selector fault real); `selw-*` a single C3 started from a seed containing the memory (including a selector detector) that `sel2-c` learned after its first C3, with baseline manifests (selector fault real).

### Experiment 1: cold C2 versus warm C2

| metric | cold C2 (cc2-a, b, c, d) | warm C2 after C1 (sim-a..d, stored) |
|---|---|---|
| tokens | 2.04M, 1.95M, 1.86M, 2.90M (median 2.0M) | 0.66M, 0.55M, 0.72M, 0.54M (median 0.61M) |
| inj->mit s | 237, 209, 177, 443 (median 223) | n/a, 104, 161, 79 (median 104) |
| last-fault s | 193, 135, 131, 983 (median 164) | 109, 73, 152, 62 (median 91) |
| solved / oracle | 4/4, 4/4 | 4/4, 4/4 |

Same flags confirmed (design check above). cc2-d is a slow outlier (983 s last-fault versus 443 s inj->mit; the cluster had etcd i/o timeouts and a controller restart during the run; kept, not infra-excluded because it resolved). Excluding it the cold medians are 2.0M, 209 s, 135 s. Cold C2 tokens (median 2.0M) match cold C1 in the simultaneous runs (median about 1.96M); warm C2 is about 3.3x cheaper, and the token ranges do not overlap (cold min 1.86M versus warm max 0.72M). Time: cold is about 2x slower by inj->mit and 1.5x by last-fault, with overlapping last-fault ranges (cold 131 to 193 versus warm 62 to 152).

### Experiment 2: selector learning

Selector detector creation after the first C3 reflection: sim-a, sim-b, sel2-c, sel3-b yes (4 of 12 first-C3 reflections pooling sim-a..d, sel-a..c, sel2-a, sel2-c, sel3-a..c); the others learned only network-policy, ConfigMap or unrelated detectors (sel3-a learned nothing: its first responder produced no diagnosis, oracle false). Quality of the four detectors:

| sequence | detector | fired on the real frontend fault | false fires on healthy services |
|---|---|---|---|
| sim-a | `service-selector-missing-pod-label` | never had the chance (no repeat) | 4 jaeger services, stale incident, failed C4 |
| sim-b | same | no chance | about 20 services in C4, run still succeeded |
| sel3-b (label reset, valid repeat) | `service_selector_ready_pod_mismatch` | NO, not on frontend in the repeat | jaeger, jaeger-agent, jaeger-collector, jaeger-query in the repeat; the same four plus geo and mongodb-rate in C1 (no selector fault) |
| sel2-c (no reset) | `service_selector_mismatch` | fault was a no-op | none in the repeat or C1 |

So answering the question as asked: when reflection creates a selector detector it usually does not help. In the only valid repeat that had one (sel3-b), it did not fire on the real fault, false-fired on four healthy jaeger services, and the repeat was not faster for `wrong_selector` (146 s first, 314 s repeat) and failed the oracle (4/5 faults with a flapping geo probe; classification: learned-detector false positive plus load, not proven causal). The correct detector from sel2-c, seeded into fresh memory with baseline manifests (`selw-a/b/c`, 3 of 3), fired before dispatch on frontend each time with no false fires on healthy services (fingerprint `frontend`; also the ConfigMap and network-policy detectors). Per-fault `wrong_selector:frontend` s: seeded 365 (selw-a, host etcd degraded, controller restarted), 64, 69 versus first C3 cold 139, 149, 219, 107, 177, 146, 247, 557 (sel-a, b, c, sel2-a, sel2-c, sel3-b, sel3-c, sel3-a anomalous) and sim 294, 144, 76, 249 (cold medians about 165 s). Seeded all-faults-resolved was 878, 112, 69 s versus cold C3 118 to 285 s excluding outliers. Same-sequence repeats without a seeded detector (sel3-a 102 s, sel3-c 85 s) were also fast, so repeat speed is not attributable to the detector.

Runs classified (excluding infra reruns): learned-detector false positive: sel3-b C3' and C1 (jaeger), sim-a C4 (earlier). Anomalous or failed: sel-b C1 (874 s, 0 responder tokens, receipt missing, oracle false), sel3-a first C3 (responder 0.20M tokens, empty diagnosis, oracle false, probes green at 557 s), sel3-c first C3 (oracle false despite 5/5 probes, 1766 s) and sel3-c C1 (2/3, `no_further_incident`, network-policy fault never repaired, oracle false), sel-c repeat (1077 s). Infra, excluded and rerun on fresh clusters: sel-c, cc2-c, cc2-d (twice), sel2-b, sel3-b, sel3-c, selw-b, selw-c (three times) for kind create or openebs/Calico failures, ControllerInstallError, etcd i/o timeouts; evidence in the `*-infra*` directories.

## Takeaways

1. Cold C2 versus warm C2: the C2 cost saving is a memory effect. Meaning: cold C2 costs about 2.0M tokens, the same as cold C1, while C2 after C1 costs about 0.6M (3.3x lower, no range overlap, n=4 vs 4); inj->mit is about 2x faster warm (104 versus 223 s median) but last-fault ranges overlap. Confidence: medium for tokens (consistent in 4 of 4, large effect, one composite and model), low for speed. Implication: report the token effect and not a speed learning curve. Next step: if a speed claim is wanted, n>=8 per cell on a quiet host.
2. The selector repeat as specified is invalid; fix and re-measure. Meaning: the first C3 responder's source fix (frontend pod label) persists through `up --redeploy`, making the repeated selector fault a no-op in 5 of 5 unreset repeats; a manifest-revert commit did not persist (controller-managed repo); removing the label from the live Deployment restores a real fault. Confidence: high (ever_red false, label in source). Implication: any repeat-of-a-fault experiment must reset application state, not just keep `.sdo`. Next step: make the harness reset the application source or live label itself.
3. Reflection-created selector detectors are rare (4 of 12) and in 3 of the 4 are harmful. Meaning: in the valid repeat the learned detector did not fire on the real fault and false-fired on healthy jaeger services (the same pattern as sim-a C4); the one correct detector fires before dispatch when present. Confidence: medium (one valid unseeded repeat, three seeded). Implication: learned incident detectors need a healthy-baseline quiet check and a replay against the originating fault before rollout; with a good detector, firing before dispatch works. Next step: add baseline-quiet and originating-fault replay validation to the detector validator.
4. No evidence that a pre-dispatch selector detector speeds `wrong_selector`: seeded 64 to 69 s (and one degraded 365 s) versus cold 76 to 294 s, but unseeded repeats were also 85 to 102 s. Confidence: low (n=3 seeded, large variance, host load). Next step: compare seeded versus unseeded repeat on the same host in alternation.

## Caveats

- n=4 cold C2, 3 valid label-reset repeats (one of which created a detector), 3 seeded runs; five unreset repeat sequences are confounded by design. Host load 6 to 45 (another user's jobs); etcd timeouts in the kind clusters caused controller restarts and many reruns (clusters cl-w100 and above), so timing noise is large. Rerun sequences are different waves and concurrency levels.
- The seeded runs reuse the `.sdo` of sel2-c at the commit after its first C3 reflection with `kubernetes/` reset to the baseline snapshot; that memory also contains that sequence's ConfigMap and network-policy detectors, which fired too.
- Warm C2 baseline is the stored `sim-a..d`, not rerun (different day). Tokens are responder plus reflection tokens; judge time is excluded. `inj->mit` is n/a where no closure receipt was recorded.
- The live label patch changes live state only; the previous responder's source change remains.
