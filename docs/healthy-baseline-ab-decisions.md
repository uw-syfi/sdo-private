# Healthy-baseline gate, live A/B: decisions and results

Status: 2026-10-01, complete (live A/B capped at pairs a to d by the orchestrator: n=4 sequences per arm; clusters cl-w130 to cl-w141 deleted). Branch `vic/exp/healthy-baseline-ab` (from `vic/exp/healthy-baseline-live`), worktree `/mnt/data/shli/sdo-worktrees/hb-ab`; `third_party/sregym` is a copy of the hb-live checkout (composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed). Predecessor: `docs/healthy-baseline-live-decisions.md`.

## Question

With the opt-in `--healthy-baseline` gate ON versus OFF and everything else equal, how often does a live sequence (C3 composite5, C3 repeat with frontend label reset, C1 composite3) end up with a learned incident detector that false-fires on a healthy service, does the gate reject such detectors live (and does the reflector then correct and re-propose a valid one), and does it cost anything in solved/oracle, time or tokens?

## Decisions

- Arms: `abon-{a..e}` (gate on) and `aboff-{a..e}` (gate off), n=5 sequences per arm planned. Same invocation as `seq_hb.sh` (images `nf5`, `--inject-before-resume`, pull late findings, 3 follow-ups, 30 s cooldown, `--reflection-guidance generalize --reflection-session fresh`, seed `lifecycle-stream`, Codex gpt-6-luna, live frontend label reset before C3' and C1). `benchmarks/sregym/experiments/healthy-baseline-ab/seq_ab.sh` differs from `seq_hb.sh` only by a `<gate: on|off>` argument that adds or omits `--healthy-baseline` (and the worktree path).
- Interleaving: `run_pairs.sh` runs one on and one off sequence concurrently as a pair (so both arms see the same host load), pairs back to back; never more than 2 sequences at once. Waits up to 30 min while load > 20. Clusters `cl-w130`+ (checked against `kind get clusters`), deleted at the end.
- Infra failures (kind/openebs/Calico, ControllerInstallError, etcd i/o timeouts before the first injection) are classified separately and rerun on fresh clusters.
- Raw runs: `/mnt/data/shli/clc-runs/{abon,aboff}-*`.

## Results

Analysis: `benchmarks/sregym/experiments/healthy-baseline-ab/analyze_ab.py <seq>...` (firing table, rejection events parsed from the cumulative controller logs, created detectors) and `replay_gate.sh <seq>...` (offline replay of each sequence's final detector set against the three healthy snapshots in `/mnt/data/shli/clc-runs/hb-live/healthy`, so a latent false-firer that never fired live is still counted). A learned detector counts as "false-firing" if it fired live on a target outside the composite's fault components, or if the final-set replay reports a healthy-snapshot violation.

### Pair a (abon-a gate on, aboff-a gate off; started 13:52, done 14:29, host load 7 to 14)

- abon-a: 3 runs all 5/5, 5/5, 3/3 oracle True. The gate rejected the first C3-repeat reflection twice (14:17:58 `service-selector-mismatch`, rule `service-selector-not-in-pod-template`; 14:20:36 same detector, rule `service-selector-label-absent-from-template`; both on Service hotel-reservation/jaeger, 3 snapshots each), with the actionable message. The reflector regenerated and the third proposal (reflection attempts 3) was accepted; the final set replays clean offline (0 violations). Learned: `empty_network_policy`, `missing_required_configmap`, `service_selector_mismatch` (final accepted form).
- aboff-a: no selector detector was created (`missing_required_configmap`, `deny_all_network_policy`); no rejection; replay 0 violations.


### Pair b (abon-b, aboff-b; 14:29 to 15:17, load 12 to 14)

- abon-b: C3 5/5; C3' 3/5 oracle False (`controller stopped for detector review: health detectors did not clear within 2m` with `user` deployment-missing and `frontend` selector health findings still active; 0.26M tokens, 0 reflection attempts; no gate rejection involved, classified as a responder/closure failure, not infra); C1 3/3. Learned: `bidirectional_network_isolation`, `missing_configmap`; no selector detector; no rejection; replay 0 violations.
- aboff-b: all runs solved (5/5, 5/5, 3/3). Learned four detectors including a selector one, `service_deployment_selector_mismatch`; it never fired live on a healthy service and the final set replays clean offline (0 violations), so this selector detector was benign.

### Pair c (aboff-c, abon-c; 15:17 onward; infra trouble on the on arm)

- Infra, excluded and rerun: abon-c (`up` failed after 600 s, hotel-reservation pods not Ready, `kube-controller-manager` crashlooping on cl-w134); abon-c2 (`up` failed, etcd i/o timeouts `127.0.0.1:2379`, mongodb pods Pending on cl-w140, host load 8 to 11); abon-c3 (cl-w141) is the third attempt and is running. Two kind-create failures in a row on the on arm only look like host disk/etcd pressure (the off sequences created clusters fine, but also took 15 min to `up`), not a gate effect: the failure is before any gate code runs (cluster deploy).
- aboff-c: C3 5/5 but oracle False (finished 799 s, 3.55M tokens), C3' 5/5 True, C1 3/3 True. Learned `required_configmap_missing`, `network_policy_direction_isolation`, `readiness_probe_port_mismatch`; no selector detector; no rejection; replay 0 violations.
- Scheduling change: pairs run as a 2-slot queue (`run_queue.sh`) so a failed infra rerun does not push a third concurrent sequence. Per the orchestrator the live A/B is capped at pairs a to d (pair d = aboff-d and abon-d; no pair e unless c/d are all infra).
- Added a cheap targeted replay (`gate_replay.py`, see below) because a harmful selector detector appeared in 1 of 4 finished live sequences.
- abon-c3 (rerun, valid): C3 5/5 True, C3' 5/5 True, C1 3/3 True. Learned `required_configmap_missing`, `deny_all_network_policy`; no selector detector; no rejection; replay 0 violations. Counts as the on-arm member of pair c.

- Pair d (abon-d, aboff-d; 16:21 to 17:13): both completed. abon-d 5/5, 5/5, 3/3 all oracle True, learned only `required_configmap_missing`. aboff-d C3 5/5 True; C3' 4/5 oracle False (same `controller stopped for detector review: health detectors did not clear within 2m` stop as abon-b C3', frontend selector fault not repaired); C1 3/3 True. No selector detector in either; no rejection; replays 0 violations. The queue for pair e was cancelled per the orchestrator's cap.

## Results: live A/B (n=4 sequences per arm, 12 composite runs per arm)

Valid sequences: abon-{a,b,c3,d} (gate on) and aboff-{a,b,c,d} (gate off). Infra reruns excluded: abon-c (`up` failed, kube-controller-manager crashloop) and abon-c2 (etcd i/o timeouts, pods Pending); abon-c3 is the valid pair-c on arm. Raw runs `/mnt/data/shli/clc-runs/{abon,aboff}-*`, aggregate `ab-agg.json`.

| seq | run | solved | oracle | last-fault s | inj->mit s | tokens (resp+refl) | per-fault s | learned detectors that fired (all on-target; none off-target) |
|---|---|---|---|---|---|---|---|---|
| abon-a | C3 | 5/5 | True | 154 | 178 | 2.00M | m-rate 117, recommendation 90, geo 85, user 85, frontend 154 | none |
| abon-a | C3' | 5/5 | True | 80 | 100 | 1.71M | m-rate 48, recommendation 43, geo 75, user 80, frontend 80 | empty-network-policy; missing-required-configmap |
| abon-a | C1 | 3/3 | True | 119 | 143 | 0.33M | m-rate 119, recommendation 114, geo 119 | empty-network-policy; missing-required-configmap |
| aboff-a | C3 | 5/5 | True | 244 | 416 | 3.50M | m-rate 223, recommendation 244, geo 165, user 165, frontend 181 | none |
| aboff-a | C3' | 5/5 | True | 101 | 128 | 1.38M | m-rate 43, recommendation 85, geo 74, user 74, frontend 101 | missing-required-configmap |
| aboff-a | C1 | 3/3 | True | 135 | 150 | 0.57M | m-rate 114, recommendation 119, geo 135 | deny-all-network-policy; missing-required-configmap |
| abon-b | C3 | 5/5 | True | 289 | 315 | 2.63M | m-rate 230, recommendation 128, geo 138, user 128, frontend 289 | none |
| abon-b | C3' | 3/5 | False | - | - | 0.26M | m-rate 43, recommendation 37, geo 380, user -, frontend - | bidirectional-network-isolation; missing-required-configmap | controller stopped for detector review: health detectors did
| abon-b | C1 | 3/3 | True | 99 | 111 | 0.40M | m-rate 47, recommendation 42, geo 99 | bidirectional-network-isolation; missing-required-configmap |
| aboff-b | C3 | 5/5 | True | 155 | 189 | 1.79M | m-rate 118, recommendation 85, geo 85, user 85, frontend 155 | none |
| aboff-b | C3' | 5/5 | True | 466 | 490 | 0.68M | m-rate 74, recommendation 85, geo 80, user 80, frontend 466 | missing-required-configmap |
| aboff-b | C1 | 3/3 | True | 52 | 69 | 1.78M | m-rate 31, recommendation 52, geo 52 | missing-required-configmap |
| abon-c3 | C3 | 5/5 | True | 171 | 197 | 1.93M | m-rate 117, recommendation 96, geo 96, user 96, frontend 171 | none |
| abon-c3 | C3' | 5/5 | True | 204 | 241 | 1.37M | m-rate 49, recommendation 118, geo 166, user 118, frontend 204 | required-configmap-missing |
| abon-c3 | C1 | 3/3 | True | 74 | 85 | 0.32M | m-rate 36, recommendation 42, geo 74 | deny-all-network-policy; required-configmap-missing |
| aboff-c | C3 | 5/5 | False | 799 | 409 | 3.55M | m-rate 500, recommendation 722, geo 272, user 799, frontend 722 | none |
| aboff-c | C3' | 5/5 | True | 85 | 111 | 1.48M | m-rate 48, recommendation 85, geo 80, user 85, frontend 0 | required-configmap-missing |
| aboff-c | C1 | 3/3 | True | 47 | 69 | 0.31M | m-rate 47, recommendation 42, geo 42 | network-policy-direction-isolation; readiness-probe-port-mismatch; required-configmap-missing |
| abon-d | C3 | 5/5 | True | 140 | 168 | 1.75M | m-rate 118, recommendation 86, geo 86, user 86, frontend 140 | none |
| abon-d | C3' | 5/5 | True | 316 | 305 | 1.13M | m-rate 66, recommendation 197, geo 316, user 197, frontend 257 | required-configmap-missing |
| abon-d | C1 | 3/3 | True | 158 | - | 0.57M | m-rate 53, recommendation 158, geo 153 | required-configmap-missing |
| aboff-d | C3 | 5/5 | True | 229 | 309 | 1.79M | m-rate 229, recommendation 148, geo 127, user 148, frontend 143 | none |
| aboff-d | C3' | 4/5 | False | - | - | 0.21M | m-rate 38, recommendation 134, geo 112, user 112, frontend - | missing-required-configmap | controller stopped for detector review: health detectors did
| aboff-d | C1 | 3/3 | True | 153 | 168 | 0.53M | m-rate 48, recommendation 153, geo 147 | missing-required-configmap |

### Per-measure tally

| measure | gate ON (n=4 seq) | gate OFF (n=4 seq) |
|---|---|---|
| (a) distinct learned incident detectors created (sum, mean/seq) | 8 (2.0): a 3, b 2, c3 2, d 1 | 10 (2.5): a 2, b 4, c 3, d 1 |
| sequences where reflection created a selector detector | 1 of 4 (abon-a) | 1 of 4 (aboff-b) |
| learned detector fired live on a service outside the composite's fault components | 0 of 4 sequences (0 of 12 runs) | 0 of 4 (0 of 12) |
| final detector set false-fires on the healthy snapshots (offline replay) | 0 of 4 | 0 of 4 |
| (d) stale incident opened by a false-firing detector | 0 | 0 |
| (b) gate rejections | 2 events, both in abon-a C3' (the same detector, 3 snapshots each) | n/a (gate off) |
| bad detector proposed and then committed | 0 (the abon-a one was rejected) | 0 (the aboff-b selector detector replays clean) |
| (c) runs with oracle True | 11 of 12 (the miss: abon-b C3', stop for detector review) | 10 of 12 (misses: aboff-c C3 oracle False with 5/5 probes, aboff-d C3' stop for detector review) |
| sequences with all three runs oracle True | 3 of 4 | 2 of 4 |
| median tokens per run, C3 / C3' / C1 | 1.96M / 1.25M / 0.36M | 2.65M / 1.03M / 0.55M |
| median inj->mit s, C3 / C3' / C1 | 188 / 241 / 111 | 359 / 128 / 110 |
| median last-fault s, C3 / C3' / C1 | 163 / 204 / 109 | 237 / 101 / 94 |
| total tokens per sequence (mean) | 3.60M | 4.40M |

Gate rejections in detail (abon-a, the only live rejection). The first C3' reflection proposed a selector detector `service-selector-mismatch`. Event 1 (14:17:58): rule `service-selector-not-in-pod-template`, violation on Service `hotel-reservation/jaeger` in each of the 3 healthy snapshots. The reflector regenerated; event 2 (14:20:36): same detector, rule `service-selector-label-absent-from-template`, again `hotel-reservation/jaeger` x 3 snapshots. Third attempt (reflection attempts = 3) was accepted and committed as `service_selector_mismatch`; its final form replays clean (0 violations) and it did not fire later, so it was quiet on healthy services but also did not fire on the real frontend fault in C3'/C1 (the frontend fault in abon-a C3' was resolved in 80 s with no learned selector detector having fired, as in most repeats). Message text (verbatim from the controller log): `detector "service-selector-mismatch" reported an active finding on healthy baseline snapshot healthy-0.json (namespace "hotel-reservation"): rule "service-selector-not-in-pod-template" on Service hotel-reservation/jaeger: A Service selector is absent from its Deployment pod template; the healthy baseline is a recorded cluster state with no fault, so an incident detector must stay quiet on it; key the predicate on the fault condition itself (a state the healthy application never has) instead of a shape that ordinary healthy resources also have`. The rejection cost two extra reflection attempts, roughly 3 min of wall time before the third was committed (14:17:58 to 14:22:42), all outside the injection-to-mitigation window.

Per-fault times: no systematic difference; the arm medians above are dominated by host noise (the off C3 median is pulled up by aboff-a 416 s and aboff-c's slow first run). Failures in the two arms are the same type (responder fails to repair `frontend`/`user` in a C3' repeat, `controller stopped for detector review`), once per arm, unrelated to the gate (abon-b had 0 reflection attempts and no rejection).

## Results: targeted reflection replay (the rate with n)

`gate_replay.py` re-runs the real reflection (SessionReflector, fresh session, guidance `generalize`, Codex gpt-6-luna, scratch clone at the incident's outcome commit; as `reflection_replay.py`), then checks each proposal with the validator's entry point without and with the baseline gate, using the three healthy snapshots from `hb-live/healthy`. Rejected proposals are retried with the same retry prompt the broker uses (validator error plus rejected diff, fresh session), up to 2 retries. The gate ON verdict of attempt 0 and the gate OFF verdict are on the same proposal (paired design, not independent arms: with the gate off the first proposal is committed as is). Closures: sel3-b incident `...137584015` (the one whose reflection created the false-firing `service_selector_ready_pod_mismatch`), sim-a `...049778733` (created `service-selector-missing-pod-label`, false-fired on jaeger and failed C4), sim-b `...587780556` (created `service_selector_mismatch`, false-fired on about 20 services). Raw: `/mnt/data/shli/clc-runs/ab-gate-replay{,-sim-a,-sim-b}`.

| closure | replays | created a selector detector | false-fires on healthy snapshots (gate OFF would commit it) | gate ON rejected | corrected on retry 1 |
|---|---|---|---|---|---|
| sel3-b first-C3 | 8 | 8 | 0 | 0 | n/a |
| sim-a | 6 | 6 | 1 (`service-selector-misses-workload-labels`, 57 violations over 19 services, 3 snapshots) | 1 | 1 (retry passed, own near-miss tests kept) |
| sim-b | 6 | 6 | 1 (`service-deployment-selector-mismatch`, `jaeger` x 3 snapshots) | 1 | 1 |
| total | 20 | 20 | 2 of 20 (10%, 95% Wilson interval about 3% to 30%) | 2 of 2 false-firers rejected; 0 of 18 good ones rejected | 2 of 2 |

Every replayed proposal passed its own Go tests (gate OFF verdict `off_pass` True in 20 of 20), so without the gate both false-firing detectors would have been committed; with it, both were rejected with the actionable message and fixed on the first retry. The false-fire rate in replay (2/20) is lower than in the original live data (3 of 4 live selector detectors false-fired: sim-a, sim-b, sel3-b), and 0 of 8 replays from the sel3-b closure reproduced its false-fire.

## Takeaways

1. The live A/B alone is inconclusive about benefit. Meaning: in 4 vs 4 sequences a selector detector was created once per arm, and the only bad proposal (abon-a, rejected twice, accepted on attempt 3) happened with the gate on; with the gate off the one selector detector created was benign, so no false firing, stale incident or oracle failure attributable to a missing gate occurred. Confidence: high that the gate fired live correctly and did not block the run (abon-a finished 3/3 runs oracle True); very low for any outcome difference (n=4 per arm, 11/12 vs 10/12 oracle, differences in time and tokens are within host noise). Implication: the gate's value is insurance against a roughly 10% per-selector-reflection false-fire rate, not a measurable live speedup. Next step: nothing more live is needed to show correctness; to measure benefit, use more replays or a larger live n (about 25 sequences would be needed to see 2 or 3 bad detectors at this rate).
2. The replay gives the rate: of 20 real reflections from three closures, 2 proposed a healthy-false-firing selector detector; both passed their own tests, both were rejected by the gate and both were fixed on the first retry; no good detector was rejected (0 of 18). Confidence: medium (n=20, but 3 closures with different rates 0/8, 1/6, 1/6, one replay environment that differs from the pod: workspace-write sandbox and no responder turn log in the brief). Implication: the gate is cheap insurance with no observed false rejections and fast self-correction (one extra reflection, about 0.5M tokens when it happens, live: 2 extra attempts in about 5 min). Next step: enable by default in the benchmark adapter once the capture is also made for the non-composite single-fault stages, and keep the rejection counter in the receipt.
3. The learning outcomes of the gate-off arm were not worse; runs with failures (`controller stopped for detector review` in abon-b and aboff-d, oracle False in aboff-c C3) are the existing responder variance in the repeat of the frontend selector fault, one in each arm.

## Caveats

- n=4 sequences per arm (planned 5, capped at 4 by the orchestrator after two infra failures on pair c); pair e not run. The on arm for pair c is a rerun (abon-c3) on a different wave; arms were interleaved as concurrent pairs except for pair c, where the on arm started 50 min later.
- Host load 7 to 20 (another user's jobs and our own concurrent replays during pair d); two kind-create failures (etcd i/o timeouts) classified as infra. Times are noisy and not a comparison of the gate's cost (capture plus publish is about 5 s per stage, measured earlier).
- "False-fired live" is checked against the composite's fault components (`fingerprint` targets); a learned detector firing on a not-faulted target would show as off-target, and none did in 24 runs; the gate-off arm's final detector sets were also replayed against the snapshots (0 violations in all 8), so the absence of live false fires is not an artifact of detectors that were simply never exercised.
- Replay: scratch clone is workspace-write, `responder_turn_log` is not passed to the brief (as in `reflection_replay.py`), the reflector may therefore be less (or more) careful than in the pod; the retry loop here emulates the broker's retry prompt, not its ledger. The corrected detectors were checked only by their own Go tests plus the gate, not replayed against the original faulted state.
- The baseline snapshots for replay and the offline final-set check come from the quiet `fastloop-w0` hotel-reservation namespace (same application and manifests), not from each run's own capture; the live runs' own capture ran for ON runs only.
