# Healthy-baseline gate, live A/B: decisions and results

Status: 2026-10-01, in progress. Branch `vic/exp/healthy-baseline-ab` (from `vic/exp/healthy-baseline-live`), worktree `/mnt/data/shli/sdo-worktrees/hb-ab`; `third_party/sregym` is a copy of the hb-live checkout (composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed). Predecessor: `docs/healthy-baseline-live-decisions.md`.

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
