# Healthy-baseline gate, live wiring: decisions and results

Status: 2026-10-01, in progress. Branch `vic/exp/healthy-baseline-live` = `vic/fix/detector-healthy-baseline` merged with `vic/exp/cold-c2-selector-learning`. Worktree `/mnt/data/shli/sdo-worktrees/hb-live`; `third_party/sregym` is a copy of the simul2 checkout (the composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed). Predecessors: `docs/detector-healthy-baseline-decisions.md` (the gate), `docs/cold-c2-selector-learning-decisions.md` (the finding).

## Questions

1. Does the gate reject the reflection-created selector detector that false-fired on four healthy jaeger services in sel3-b, with an actionable message, while the genuinely useful seeded detector (selw-a/b/c `service_selector_mismatch`) still passes?
2. Can the snapshots be captured and supplied in a live run without touching `controller/runtime`?
3. (Only if images can be rebuilt) With the gate on, is the false-firing detector rejected or never created in a live C3, C3 (label reset), C1 sequence, and does the run complete?

## Decisions

(filled in as work proceeds)
