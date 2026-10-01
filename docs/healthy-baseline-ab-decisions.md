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

