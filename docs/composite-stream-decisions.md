# Composite stream (10 composites, all fixes on): decisions and results

Status: 2026-10-01, in progress. Branch `vic/exp/composite-stream` (from `vic/exp/healthy-baseline-ab`), worktree `/mnt/data/shli/sdo-worktrees/composite-stream`. Predecessors: `docs/healthy-baseline-ab-decisions.md`, `docs/cold-c2-selector-learning-decisions.md`, `docs/simultaneous-composites-decisions.md`, `docs/composite-learning-curve-decisions.md`, `docs/nfault-composites-decisions.md` (these live in sibling worktrees).

## Questions

1. With every fix on, does cost per composite (tokens, time) trend down along a 10-composite stream (per-position medians across sequences)?
2. Does memory growth (incident detectors / playbooks) stay compact?
3. Resolution rate versus Codex + verify.

## Decisions

- Stream (fixed, same for every sequence): `C1 C2 C3 C1 C4 C2 C3 C5 C1 C4` with C1 = `composite3_hotel_geo_rate_recommendation`, C2 = `composite3b_hotel_profile_mongodb_geo_recommendation`, C3 = `composite5_hotel_geo_rate_recommendation_frontend_user`, and two new hand-registered variants (one fault per Deployment, no generator): C4 = `composite3c_hotel_rate_mongodb_geo_user` (readiness rate, ConfigMap mongodb-geo, network policy user), C5 = `composite4_hotel_profile_rate_recommendation_frontend` (readiness profile, ConfigMap mongodb-rate, network policy recommendation, wrong selector frontend). ConfigMap targets are limited to mongodb-geo and mongodb-rate by the injector, so variation comes from the readiness and network-policy targets and from mixing the other families' components. Positions 4, 6, 7, 9, 10 repeat a composite (C1 x3, C2 x2, C3 x2, C4 x2), position 8 (C5) is new but reuses components.
- Arm "SDO all-on": `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, `--healthy-baseline`, images `nf5`, seed `lifecycle-stream`, Codex gpt-6-luna, fast loop, probe-graded (no LLM judge, no judge time in any timing). Script `benchmarks/sregym/experiments/composite-stream/seq_stream.sh` = `healthy-baseline-ab/seq_ab.sh` with gate on, pull, 3 follow-ups fixed and the worktree path changed. One persistent controller and `.sdo` per sequence; app redeployed (`up --redeploy`) before each composite; the live frontend pod label that an earlier responder added is removed before every composite after the first so a repeated `wrong_selector:frontend` is a real fault (selector-repeat design problem, `cold-c2-selector-learning-decisions.md`). Other persisted source fixes are not reset; inert injections are detected from `ever_red` per fault and reported.
- Baseline: Codex gpt-6-luna + verify protocol. Stored results reused for C1, C2, C3 (not rerun): C1 0/4 (verify, `nfault`), C2 0/2 (`composite-learning-curve`), C3 0/4. New Codex runs only on C4 and C5, n=2 each, on a freshly redeployed app per run (`codex_stream.sh`).
- Concurrency: at most 2 jobs at once (`run_stream_queue.sh`), waits up to 30 min while load > 20. Clusters `cl-w150`+ for first attempts, `cl-w170`+ for infra reruns; all deleted at the end. Infra failures (kind create, openebs/Calico, `ControllerInstallError`, etcd i/o timeouts before the first injection) are classified separately and rerun.
- Raw runs: `/mnt/data/shli/clc-runs/cstream-*` (SDO) and `cstream-codex*`.
- The submodule commit adding C4/C5 (`03b1df58`) lives in a private copy of the submodule git dir (`/mnt/data/shli/sdo-worktrees/.composite-stream-sregym-gitdir`, branch `vic/exp/composite-stream`); no remotes changed.

## Results

(to be filled per finished sequence)
