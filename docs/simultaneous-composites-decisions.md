# Simultaneous composites: decisions and results

Status: 2026-10-01. Branch `vic/exp/simultaneous-composites` (from `vic/exp/composite-learning-curve`). Predecessor: `docs/composite-learning-curve-decisions.md`.

## Question

Memory showed no benefit on composites because faults are injected over about 16 s and the controller dispatches about 1 s after the first finding, so learned detectors for later faults can only fire after dispatch. If every component fault is present before the controller first observes the namespace, do learned detectors for all faults fire before dispatch, does the first request carry every finding, and do repeats get faster and need no follow-up?

## Decision: how to inject simultaneously

Options: (a) scale the controller to 0 and back, (b) inject all, then install the controller, (c) use the controller's existing maintenance (pause/resume) switch.

Chosen: (c), as an opt-in `--inject-before-resume` flag on `fastloop run`, implemented by `InjectBeforeResumeOps` (`benchmarks/sregym/fastloop/simultaneous.py`), a `ClusterOps` wrapper. The persistent stage normally resumes the controller, waits for an all-clear baseline, then injects. With the wrapper, the stage's resume is deferred: the controller is paused (new maintenance generation, acknowledged in the controller log), the whole composite is injected, then the controller is resumed. Per `set_controller_maintenance`, a resume makes the controller re-list the namespace and evaluate every detector once, so all faults are seen in the first evaluation. Reasons: no production change and no image rebuild; works for the first composite (fresh install) and later ones (reuse); the persistent controller, its memory and incident accounting stay untouched. Rejected: scale to 0 (loses the controller pod identity the persistent state checks) and inject-then-install (only works for the first composite). Sequential injection stays the default. The baseline all-clear wait is skipped in this mode (the baseline is by construction not clear).

Caveat: injecting still takes about 16 s, but during it the controller is paused; the clock for inject->mitigation starts after injection finishes, as before.

## Setup

Same stream as the sequential learning-curve runs: C1 composite3, C2 composite3b, C3 composite5, C4 = C1 repeat; one persistent controller and `.sdo` memory per sequence, app redeployed before each composite, `--late-findings pull --max-follow-ups 3`, `--reflection-guidance generalize --reflection-session fresh`, Codex gpt-6-luna, images `nf4` (no rebuild), fast loop, probe-graded. New arm: `--inject-before-resume`. Sequences `sim-a` (cluster `cl-w80`) and `sim-b` (`cl-w81`), concurrently, in `/mnt/data/shli/clc-runs/sim-{a,b}` (script `seq_sim.sh`, aggregator `aggregate_sim.py`). The sequential comparison is the stored `pf-a`/`pf-b` (not rerun). Submodule: the composite3b commit exists only in the local submodule branch of the `composite-lc` worktree; `third_party/sregym` here is a copy of that checkout (limitation: not fetchable from a remote).

## Results

Four simultaneous sequences (n=4: `sim-a` to `sim-d`, clusters `cl-w80` to `cl-w83`, run as two waves of two; sequential comparison is the stored `pf-a`/`pf-b`, n=2). Host load was 5 to 11 during the runs; no infrastructure failures. Raw: `/mnt/data/shli/clc-runs/{sim-a,sim-b,sim-c,sim-d}`; full per-run table: `/mnt/data/shli/clc-runs/cmp_table.md` (from `aggregate_sim.py`). `last-fault s` = injection end to the last fault's final green probe; `inj->mit s` = controller's injection to last successful repair (n/a where no receipt closure was recorded). Learned-detector columns use only firings after each composite's own injection start (the firing log is cumulative; `composite_sequence` now filters by `since`, so the sequential rows were recomputed with the same filter and are unchanged).

### Per run (simultaneous)

| seq | C | probe-resolved | oracle | last-fault s | inj->mit s | tokens | learned detector fired before dispatch (geo/readiness, configmap, netpol) | incident detectors/playbooks after |
|---|---|---|---|---|---|---|---|---|
| sim-a | C1 | 3/3 | True | 130 | 107 | 3.19M | none / none / none | 3/4 |
| sim-a | C2 | 3/3 | True | 109 | n/a | 0.66M | no (profile) / yes / yes | 3/4 |
| sim-a | C3 | 5/5 | True | 294 | 318 | 0.74M | no / yes / yes | 4/5 |
| sim-a | C4 | 0/3 | False | - | - | 0.18M | stale incident, see below | 4/5 |
| sim-b | C1 | 3/3 | True | 135 | 130 | 2.38M | none | 3/4 |
| sim-b | C2 | 3/3 | True | 73 | 104 | 0.55M | yes / yes / yes | 3/4 |
| sim-b | C3 | 5/5 | True | 144 | 160 | 1.35M | yes / yes / yes | 4/5 |
| sim-b | C4 | 3/3 | True | 57 | 103 | 0.59M | yes / yes / yes | 4/5 |
| sim-c | C1 | 3/3 | True | 131 | 64 | 1.36M | none | 2/3 |
| sim-c | C2 | 3/3 | True | 152 | 161 | 0.72M | no / yes / yes | 2/3 |
| sim-c | C3 | 5/5 | True | 76 | 86 | 0.87M | no / yes / yes | 2/3 |
| sim-c | C4 | 3/3 | True | 79 | 92 | 0.42M | no / yes / yes | 2/3 |
| sim-d | C1 | 3/3 | True | 68 | 73 | 1.53M | none | 1/1 |
| sim-d | C2 | 3/3 | True | 62 | 79 | 0.54M | no / yes / no | 1/1 |
| sim-d | C3 | 5/5 | True | 249 | 277 | 0.86M | no / yes / no | 1/1 |
| sim-d | C4 | 3/3 | True | 58 | 100 | 0.93M | no / yes / no | 1/1 |

"no" for a kind means that sequence never created a learned detector of that kind (sim-c: no readiness detector; sim-d: only a ConfigMap detector); where a learned detector for the fault's kind existed it fired before dispatch in 21 of 22 opportunities (the exception: the profile readiness fault in sim-a C2). `wrong_selector` and `resource_request` never fired a learned detector before dispatch (see question 4).

### Simultaneous versus sequential

| metric | sequential (pf-a, pf-b) | simultaneous (n=4) |
|---|---|---|
| probe-resolved composites | 8/8 | 15/16 (the miss is sim-a C4, a stale incident) |
| official oracle | 7/8 | 15/16 |
| C1 (cold) inj->mit s | 235, 236 | 107, 130, 64, 73 (median 90) |
| C1 (cold) last-fault s | 188, 136 | 130, 135, 131, 68 (median 131) |
| C2 inj->mit s / tokens | 97, 92 / 1.64M, 1.35M | 104, 161, 79 (+1 n/a) / 0.66M, 0.55M, 0.72M, 0.54M |
| C3 inj->mit s | 210, 490 | 318, 160, 86, 277 |
| C4 inj->mit s | 91 (+1 n/a) | 103, 92, 100 (+1 failed) |
| C4/C1 inj->mit | 0.39 (pf-a; pf-b n/a) | 1.44 (c), 1.37 (d), 0.79 (b); a n/a |
| C2/C1 inj->mit | 0.41, 0.39 | 0.80 (b), 2.54 (c), 1.09 (d); a n/a |
| C4/C1 last-fault | 0.37, 0.52 | 0.42 (b), 0.61 (c), 0.85 (d); a n/a |
| learned detector (existing for the kind) fired before dispatch | 0 of 12 for ConfigMap and network policy, 2 of 6 for readiness | 21 of 22 |
| follow-up incidents | 0 | 0 |

### Answers to the open questions

1. **Does simultaneous injection add anything beyond the C1 to C4 speedup already seen sequentially?** Mechanism: yes, as predicted. The first evaluation after resume carries every fault's health findings (6 activations for 3 faults, 9 for 5) and, from C2 on, the learned ConfigMap and network-policy detectors fire in that same evaluation, before dispatch (21 of 22, versus 0 of 12 sequentially). Outcome: not a repeat speedup. By inj->mit, C4 was slower than C1 in two of three valid sequences (1.4x) and faster in one (0.79); by last-fault time it was faster in all three (0.42 to 0.85), but the sequential arm shows the same (0.37 to 0.52), and the cold C1 itself is much faster under simultaneous injection (median inj->mit 90 s versus 235 s) because the cold responder gets all findings at once, leaving less to save. What simultaneous injection does add is cost: C2 token cost fell from 1.35 to 1.64M to 0.54 to 0.72M (consistent in 4 of 4), and C1 to C4 are not meaningfully different otherwise. Memory still has no demonstrable repeat-speed benefit; it showed a cost effect on C2.
2. **sim-a C4 (`detector_review_required`, 0/3 by probes and oracle; the faults were never repaired).** Evidence: C4's incident `...704353` was detected at 07:50:59, three minutes before the C4 injection at 07:54:11. It was opened at the end of C3, after C3's reflection rolled out a new learned detector (`service-selector-missing-pod-label`) and the controller relaunched: that detector fired on four jaeger services of the healthy application (`detector_firings.jsonl`: four `activated` events at 07:50:59, `no_incident`, then `batched` into incident `...704353`). A responder ran on it (0.18M tokens; I did not read its transcript), and by the time C4's faults were injected (controller paused then resumed at 07:54:27) the stage was still waiting on that stale incident; the controller then reported "health detectors did not clear within 2m after responder completion" because C4's faults arrived after the responder had finished, and stopped for detector review. The end-of-run probes show all three faults still red and the oracle false. Classification: a learned-detector false positive plus a harness window (the controller stays active between the C3 verified closure and the pause, which is when the new detector rolled out; inferred from the timestamps, not from a pause record), not infrastructure, and not caused by the injection mode. The same detector class in sim-b fired on about 20 services during C4 (39 activations before dispatch) without harming that run.
3. **sim-a C3 294 s.** All four non-selector faults were green by 91 s (ConfigMap and network policy at 74 s, readiness 91 s, user resource request 96 s). `wrong_selector:frontend` stayed red until 294 s: the controller log shows exactly one active finding from 07:44:02 to 07:47:18 and a single responder session working through it (no follow-up incident, no second dispatch). So it is the slow resolution of the hardest fault in one responder pass, not a detection or batching problem. The same fault took 144 s (sim-b), 76 s (sim-c), and 249 s (sim-d); C3's last-fault time is dominated by this fault's variance (n=4: 76 to 294 s). Also note sim-a's responder-turn count of 2 in C3 is the reflection session, not a follow-up.
4. **wrong_selector and resource_request detectors.** `wrong_selector`: a learned detector was created in sim-a and sim-b (`service-selector-missing-pod-label`, `service_selector_mismatch`), both at the end of C3 reflection. It could never fire before dispatch in this protocol because C3 is the only composite with a selector fault and the sequence never repeats it (C4 = C1), so the detector is structurally unreachable; when it did run (C4) it was a false positive on healthy services. sim-c and sim-d never created one at all. `resource_request`: no detector or playbook was created in any of the six sequences (sequential and simultaneous); I did not inspect reflection transcripts for why (health `deployment-unavailable` already surfaces it, which may make the generalize reflection see no gap). A sequence that repeats C3 (for example C1, C3, C3) is needed to test selector learning.

## Takeaways

1. **Simultaneous injection works as a mechanism, and the first request carries all findings.** Meaning: with all faults present at first evaluation, every health finding and every existing learned detector reach the first request; no follow-up incident was needed in 16 of 16 runs (the metric is only follow-up incidents; both arms also 0). Confidence: high for the mechanism (direct evidence in all 16 firing logs), low for the follow-up claim (sequential arms also had 0 by this metric). Implication: the structural obstacle named in the learning-curve doc is removed. Next step: none for the mechanism.
2. **Memory still does not make repeats faster; it makes second composites cheaper.** Meaning: C2 cost 0.54 to 0.72M tokens (4 of 4) versus 1.35 to 1.64M sequentially, and learned detectors do pre-empt dispatch, but inj->mit did not improve on repeats (C4/C1 0.79, 1.37, 1.44; C2/C1 0.8, 1.09, 2.54), and cold C1 is already fast when findings arrive together. Confidence: low to medium (n=3 to 4, per-composite variance of 2x to 4x, no controlled cold control for the C2 token effect since C2 differs from C1). Implication: do not claim a speed learning curve from this protocol; the cost effect needs a cold-C2 control. Next step: run C2 cold (fresh memory) simultaneous, n>=4, and a C1, C3, C3 sequence for selector learning.
3. **Reflection can create learned detectors that hurt.** Meaning: the selector detector created after C3 fired on healthy services, created a stale incident and one failed composite (sim-a C4) and 39 pre-dispatch activations in sim-b C4. Confidence: medium (one failure, one near-miss). Implication: learned incident detectors need a baseline-quiet check on the healthy application before rollout (and the harness should pause the controller before the post-closure redeploy). Next step: add that validation to the detector validator, or count the failure as a memory regression in the learning-curve report.

## Caveats

- n=4 simultaneous, n=2 sequential (reused), different days of load; probes are the primary metric, the official oracle agreed in all 16 simultaneous runs (including sim-a C4).
- The submodule commit for composite3b exists only on the local submodule branch of the `composite-lc` worktree; `third_party/sregym` here is a copy of that checkout, so a clean clone cannot reproduce C2 without that commit.
- `inj->mit` is n/a where the broker recorded no closure receipt (sim-a C2 and the earlier pf-b C4); last-fault time is always available.
- The firing log is cumulative across a persistent sequence. The earlier learning-curve doc's learned-detector table was computed from the unfiltered log; recomputing with the per-composite filter gave identical rows for pf-a and pf-b.
