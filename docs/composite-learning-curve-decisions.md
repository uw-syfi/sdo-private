# Composite learning curve: decisions and results

Status: 2026-10-01. Branch `vic/exp/composite-learning-curve` (from `vic/exp/nfault-composites`, which already contains `vic/fix/redispatch-residual-findings`, so no merge conflicts; the follow-up flag plumbing in `controller/builder/check_cli.py` was already present). Images `nf4` built from this head (`SDO_IMAGE_TAG=nf4 BUILDX_BUILDER=sdo-example scripts/build_sdo_images.sh`, succeeded). Predecessor: `docs/nfault-composites-decisions.md`.

## Question

Two fixes made SDO beat Codex on composites (pull-before-act late findings; opt-in follow-up responders). Untested: does SDO's memory help on composites, and do the two fixes compose?

## Setup

- Hand-registered stream of four composites on Hotel Reservation, same persistent controller and `.sdo` memory across the sequence, app redeployed (`fastloop up --redeploy`) before each composite:
  - C1 = `composite3_hotel_geo_rate_recommendation`: readiness(geo), configmap(mongodb-rate), netpol(recommendation).
  - C2 = `composite3b_hotel_profile_mongodb_geo_recommendation` (new, registered here): readiness(profile), configmap(mongodb-geo), netpol(recommendation). `reservation` has no Deployment, so `profile` is the readiness target.
  - C3 = `composite5_hotel_geo_rate_recommendation_frontend_user` (5 faults).
  - C4 = C1 repeated.
- Arms (all `--reflection-guidance generalize --reflection-session fresh`, Codex gpt-6-luna, fast loop, probe-graded, no LLM judge): `pf` = `--late-findings pull --max-follow-ups 3` (two full sequences pf-a, pf-b); `po` = `--late-findings pull` only (two full sequences po-a, po-b). Each sequence ran on its own kind cluster (workers 70 to 75), concurrently, on a shared host (load 7 to 26).
- Codex arms were not rerun for C1 and C3 (stored: `docs/nfault-composites-decisions.md`); Codex plain and verify were run on C2 (n=2 each).
- Tooling added: C2 registration (submodule `composed_failures.py`, `fault_tracker.py`, tests) and `benchmarks/sregym/analysis/composite_sequence.py` (learned-detector-versus-dispatch per fault, memory counts from the workspace git history, with a test).
- Raw runs: `/mnt/data/shli/clc-runs/{pf-a,pf-b,pf-c,po-a,po-b,codex-c2}`, scripts `seq.sh`, `codex.sh`, `aggregate.py`.

## Results

Columns: `probe-resolved` is the read-only per-fault probes at the end of the run (the primary metric); `official oracle` is the SREGym mitigation oracle on the end state; `last-fault s` is seconds from end of injection to the start of the last fault's final green run; `inj->mit s` is the controller's own injection-to-mitigation time (excludes reflection); tokens are responder plus reflection; `learned detector vs dispatch` is the first activation of a non-health (learned) detector for that fault's component relative to the first dispatch (`none` = no learned detector fired).

| seq | C | probe-resolved | official oracle | last-fault s | inj->mit s | tokens (resp+refl) | per-fault resolution s | learned detector vs dispatch | incident detectors / playbooks after |
|---|---|---|---|---|---|---|---|---|---|
| pf-a | C1 | 3/3 | True | 188 | 235 | 1.29M | mongodb-rate 130, recommendation 57, geo 188 | geo none, mongodb-rate none, recommendation none | 2/3 |
| pf-a | C2 | 3/3 | True | 89 | 97 | 1.64M | mongodb-geo 78, recommendation 73, profile 89 | profile none, mongodb-geo after, recommendation after | 3/4 |
| pf-a | C3 | 5/5 | False | 172 | 210 | 1.18M | mongodb-rate 86, recommendation 86, geo 81, user 140, frontend 172 | geo before, mongodb-rate after, recommendation after, frontend none, user none | 3/4 |
| pf-a | C4 | 3/3 | True | 70 | 91 | 0.48M | mongodb-rate 65, recommendation 70, geo 60 | geo before, mongodb-rate after, recommendation after | 3/4 |
| pf-b | C1 | 3/3 | True | 136 | 236 | 2.35M | mongodb-rate 130, recommendation 62, geo 136 | geo none, mongodb-rate none, recommendation none | 3/4 |
| pf-b | C2 | 3/3 | True | 104 | 92 | 1.35M | mongodb-geo 57, recommendation 52, profile 104 | profile after, mongodb-geo after, recommendation after | 3/4 |
| pf-b | C3 | 5/5 | True | 145 | 490 | 0.91M | mongodb-rate 38, recommendation 54, geo 54, user 129, frontend 145 | geo after, mongodb-rate after, recommendation after, frontend none, user none | 3/4 |
| pf-b | C4 | 3/3 | True (receipt missing) | 70 | - | 0.00M | mongodb-rate 60, recommendation 53, geo 70 | geo after, mongodb-rate after, recommendation after | 3/4 |
| po-a | C1 | 3/3 | True | 57 | 51 | 1.15M | mongodb-rate 57, recommendation 36, geo 57 | geo none, mongodb-rate none, recommendation none | 1/4 |
| po-a | C2 | 3/3 | True | 68 | 136 | 1.46M | mongodb-geo 63, recommendation 47, profile 68 | profile none, mongodb-geo after, recommendation none | 2/4 |
| po-a | C3 | 4/5 | False (stopped: detector review) | - | 175 | 1.09M | mongodb-rate 112, recommendation 150, geo 144, user 182, frontend - | geo none, mongodb-rate after, recommendation after, frontend none, user none | 2/4 |
| po-a | C4 | 3/3 | True | 84 | 70 | 1.63M | mongodb-rate 58, recommendation 58, geo 84 | geo none, mongodb-rate after, recommendation after | 3/4 |
| po-b | C1 | 3/3 | True | 122 | 155 | 1.76M | mongodb-rate 122, recommendation 75, geo 75 | geo none, mongodb-rate none, recommendation none | 3/4 |
| po-b | C2 | 3/3 | True | 59 | 35 | 1.02M | mongodb-geo 59, recommendation 53, profile 48 | profile before, mongodb-geo after, recommendation after | 3/4 |
| po-b | C3 | 4/5 | False (stopped: detector review) | - | 142 | 0.55M | mongodb-rate 70, recommendation 64, geo 70, user 115, frontend - | geo before, mongodb-rate after, recommendation after, frontend none, user none | 3/4 |
| po-b | C4 | 3/3 | False | 317 | 319 | 0.57M | mongodb-rate 317, recommendation 304, geo 317 | geo before, mongodb-rate after, recommendation after | 3/4 |

### Summary per composite (full sequences only, n=2 per arm)

| composite | pull + follow-up (pf) | pull only (po) | Codex plain | Codex + verify |
|---|---|---|---|---|
| C1 (3 faults, cold) | 2/2 | 2/2 | 0/4 | 0/4 |
| C2 (3 faults, new targets) | 2/2 | 2/2 | 0/2 | 0/2 |
| C3 (5 faults) | 2/2 (one official-oracle failure on geo readiness) | 0/2 (4/5, `wrong_selector` never resolved; controller stopped for detector review) | 0/4 | 0/4 |
| C4 (C1 repeat, warm) | 2/2 (one closure failed, see caveats) | 2/2 (one official-oracle failure) | - | - |
| total probe-resolved | 8/8 | 6/8 | 0/10 | 0/10 |
| total official oracle | 7/8 | 5/8 | 0/10 | 0/10 |

Codex on C2 (new, `/mnt/data/shli/clc-runs/codex-c2`): plain 2/3 and 2/3 faults, verify 2/3 and 2/3. Every run left the network policy unresolved, as on C1 and C3 (16 of 16 earlier). Tokens 0.34M to 0.57M. Stored Codex C1: plain 0/4 (2.0/3), verify 0/4 (1.8/3); C3: plain 0/4 (3.5/5), verify 0/4 (3.8/5).

### Memory growth

Incident detectors / playbook directories in `.sdo` after each composite (from the workspace git history):

| sequence | start | after C1 | after C2 | after C3 | after C4 |
|---|---|---|---|---|---|
| pf-a | 0/1 | 2/3 | 3/4 | 3/4 | 3/4 |
| pf-b | 0/1 | 3/4 | 3/4 | 3/4 | 3/4 |
| po-a | 0/1 | 1/4 | 2/4 | 2/4 | 3/4 |
| po-b | 0/1 | 3/4 | 3/4 | 3/4 | 3/4 |

Learning saturates after C1 or C2 (two to three incident detectors: required ConfigMap, network policy, readiness). C3's reflection added nothing in any sequence, and no detector for `wrong_selector` or `resource_request` was ever created.

### Learned detector versus dispatch (C2 to C4, all four sequences)

| fault kind | learned detector fired before dispatch | after dispatch | never |
|---|---|---|---|
| readiness (first injected) | pf 2, po 3 | pf 3 | pf 1, po 3 |
| configmap | 0 | pf 6, po 6 | 0 |
| network policy | 0 | pf 6, po 5 | po 1 |
| wrong selector, resource request | 0 | 0 | 4 each (pf 2, po 2) |

### C4 versus C1 (same composite, cold versus warm)

| sequence | last fault s (C1 to C4) | inj->mit s (C1 to C4) | responder tokens (C1 to C4) |
|---|---|---|---|
| pf-a | 188 to 70 | 235 to 91 | 0.88M to 0.48M |
| pf-b | 136 to 70 | 236 to n/a (closure failed) | 1.28M to n/a |
| po-a | 57 to 84 | 51 to 70 | 0.57M to 0.80M |
| po-b | 122 to 317 | 155 to 319 | 0.82M to 0.57M |

Wall time per incident includes reflection (0.4 to 0.9 min of it) and is not used here.

## Decisions

1. Hand-registered C2 rather than a generator; `profile` is the readiness target.
2. Probe resolution is the primary metric (as before); the official oracle is reported alongside because it disagreed in 4 of 16 SDO runs (geo readiness oracle after the responder swapped the gRPC probe for a TCP probe, a network-policy oracle failure on po-b C4 at 317 s, and the two 4/5 runs).
3. Sequences ran concurrently on separate kind clusters; the app is redeployed (not the controller) between composites so injections are never inert (no inert injection recorded).
4. Codex was rerun only for the new composite (C2).

## Takeaways

1. **The two fixes compose, and follow-ups matter at 5 faults.** pf resolved 8/8 composites by probes (7/8 by the official oracle), po 6/8 (5/8); all of the gap is C3, where po stopped on `detector_review_required` with `wrong_selector` unresolved in both sequences and pf finished 5/5 in both (172 and 145 s). Confidence: low to medium (n=2 per arm, but the C3 outcome is consistent across both sequences and matches the cold evidence that follow-ups catch residual findings). Implication: pull gets most faults in the first task, follow-up is the backstop for what is still active; ship both. Pull alone is not enough at 5 faults (earlier cold pull C5 1/1 does not generalize). Next step: n>=5 on the 5-fault composite and a 7-fault one, pf versus po.
2. **SDO beats Codex on every composite, memory or not.** 14 of 18 SDO runs in pf/po are fully resolved by probes versus 0 of 20 Codex runs; Codex never resolves the network policy (now 20 of 20 runs across C1 to C3 for each Codex arm family), and costs 0.3M to 0.6M tokens versus 0.5M to 2.4M for SDO. Confidence: medium-high on the direction. Implication: the advantage comes from the standing health detector plus the two fixes, not from memory. Next step: report cost per resolved fault.
3. **Memory has not been shown to help on composites.** C4 was faster than C1 in both pf sequences (probe time 188 to 70 s and 136 to 70 s; inj->mit 235 to 91 s in pf-a) but slower in both po sequences (57 to 84 s, 122 to 317 s). With cold C1 itself spanning 57 to 188 s, this is within run-to-run noise. Learned detectors mostly fire after dispatch: the controller dispatches about 1 s after the first finding, while the faults are injected over about 16 s, so only the first-injected fault (geo) can be known before dispatch, and a learned detector adds nothing the health detector had not already surfaced there. Confidence: medium that memory effect is small or absent in this protocol; low on any positive effect. Implication: with sequential injection the learned-detector route is structurally unable to pre-empt the pull and follow-up mechanisms; real simultaneous faults would differ. Next step: either inject all faults before the controller observes the namespace, or add a short batching delay before the first dispatch, then rerun C2 to C4 and measure first-task coverage; and make reflection create detectors for `wrong_selector` and `resource_request`.
4. **Learning saturates quickly and some incidents teach nothing.** Memory stops growing after C1 or C2; po C3 never reached reflection (the controller stopped for detector review), and C3 did not add detectors in pf either. Confidence: medium. Implication: a closed incident is the unit of learning, so runs that stop on detector review forfeit memory; follow-ups also fix this. Next step: reflect on a stopped incident, or count it in the learning curve explicitly.

## Caveats

- n=2 per arm and per composite position, stochastic agents, one host with load 7 to 26 (shared with other users' kind clusters), concurrent lanes. No LLM judge; fast loop, not the SREGym conductor.
- pf-c: its C1 failed with `ControllerInstallError` (service account `default` not found in `hotel-reservation-sdo` during cluster start-up under load), so pf-c C2 ran cold and is not part of the C1 versus C4 comparison. Its rows are appended below.
- pf-b C4: faults were resolved (probes and official oracle green) but the broker rejected the closure ("a confirmed repair without source changes requires at least one successful recorded repair action", 8 attempts), so the incident receipt is missing and tokens read 0; that closure produced no memory commit. This is a separate broker bug candidate for recorded-actions closure of a re-fixed composite.
- Official oracle disagreed with probes in pf-a C3 (geo readiness, probe replaced by TCP) and po-b C4 (network-policy oracle); both are counted as resolved by probes and unresolved by the oracle in the tables.
- Wall times include reflection and controller drain; only inj->mit and per-fault probe times are comparable across arms.
- pf-c and the `up`/redeploy path had two infrastructure retries (etcd timeout on cluster creation, a `wrk2-job` deletion timeout); neither affected a recorded measurement.
- Submodule commit for C2 exists on the local submodule branch `vic/exp/composite-learning-curve` only (the submodule remote is a local path).
