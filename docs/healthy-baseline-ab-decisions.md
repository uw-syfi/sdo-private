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

(filled in as sequences finish)
