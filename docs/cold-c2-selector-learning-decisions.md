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

Interim (wave 1; cc2-a, cc2-b, sel-a, sel-b finished; more running).

- Cold C2 (fresh memory, composite3b first): cc2-a 3/3, 193 s last-fault, inj->mit 237 s, 2.04M tokens; cc2-b 3/3, 135 s, 209 s, 1.95M tokens. Stored C2 after C1 (sim-a..d): 0.54 to 0.72M tokens, inj->mit 79 to 161 s. So far the token saving looks like a memory effect (2.0M cold versus 0.6M warm).
- Selector learning, no reset (sel-a, sel-b): neither C3 reflection created a selector detector (sel-a learned a deny-all network-policy detector, sel-b a required-ConfigMap detector). In the repeat of C3 `wrong_selector:frontend` was never red (`ever_red` false, green at t=0.3 s): the first C3's responder committed the fix to the application source (`kubernetes/frontend/frontend-deployment.yaml` gained the `current_service_name: frontend` pod label, commit `6b11ec9`-style), `up --redeploy` redeploys from that source, and the injected selector then matches the pods. The repeat therefore tests nothing about detectors; it measures a persistent source fix. A reset variant (`seq_sim3.sh`, sequences `sel2-*`) restores `kubernetes/` to the baseline snapshot before each redeploy so the selector fault is real again.
- Infra: sel-c, cc2-c, sel2-a, sel2-b hit kind/etcd timeouts or Calico install failures with several clusters starting at once (host load 10 to 45, shared with another user); they are classified infra, kept as `*-infra` directories, and re-run with fewer concurrent clusters.

