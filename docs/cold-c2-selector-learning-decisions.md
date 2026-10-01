# Cold-C2 control and selector learning: decisions and results

Status: 2026-10-01, in progress. Branch `vic/exp/cold-c2-selector-learning` (from `vic/exp/simultaneous-composites`). Predecessor: `docs/simultaneous-composites-decisions.md`.

## Questions

1. Is the C2 token saving seen after C1 (0.54 to 0.72M versus 1.35 to 1.64M sequential) a memory effect? Control: run C2 (composite3b) FIRST on a fresh controller and `.sdo` (seeded identically to C1 of the earlier sequences), n>=4, and compare tokens and inj->mit to the stored `sim-a..d` C2 rows.
2. Does a selector detector, created at the end of the C3 reflection, fire before dispatch on a repeated C3, and does the repeat resolve `wrong_selector:frontend` faster? Sequence: C3 (composite5), C3' (composite5 again), then C1 (composite3), n>=3. Also record any later learned detector that false-fires on the healthy application and classify those runs.

## Decisions

- Same settings as the simultaneous arm: `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, images `nf4` (no rebuild), Codex gpt-6-luna, fast loop, probe-graded. Invocation reused verbatim from `seq_sim.sh` (copy `seq_sim2.sh`, only the worktree path differs) and the aggregator from `aggregate_sim.py` (copy `aggregate_sim2.py`, extra sequence names, output `agg_cmp2.json`).
- Worktree `/mnt/data/shli/sdo-worktrees/simul2`; `third_party/sregym` is a copy of the simul checkout (the composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed).
- Sequences are named `cc2-{a..d}` (cold C2, one composite each; the run id suffix is `-C1`) and `sel-{a..c}` (run ids `-C1` = C3, `-C2` = C3 repeat, `-C3` = C1). Clusters `cl-w90`+ (checked against `kind get clusters`), deleted at the end.
- Codex and sequential arms are not rerun; comparisons use the stored `sim-a..d` and `pf-a/b` rows.
- Load policy: wait up to 30 min if `uptime` load > 20; timeouts and install failures are classified as infra and listed separately.

## Results

(pending)
