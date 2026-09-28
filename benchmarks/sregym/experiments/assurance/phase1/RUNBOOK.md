# Phase-1 live assurance matrix: runbook

This runbook covers the phase-1 live matrix in `PLAN.md` (d): 8 lanes
(`assure-w0`..`assure-w7`), about 2 h wall clock, comparing SDO against a
memoryless Codex baseline (stock and + verify). It assumes the launcher
(`launch.sh` / `benchmarks.sregym.assurance.phase1_launch`), the image
rebuild step (`benchmarks.sregym.assurance.rebuild_v010_images`) and the
analysis step (`benchmarks.sregym.assurance.phase1_analyze`) built alongside
this file, on branch `vic/exp/phase1-launch`.

**No LLM or Codex run may start before the weekly quota resets at
2026-10-03 18:19 UTC, and only if `used_percent <= 50%` at that point
(PLAN.md (d)).**

## Preconditions

Check every one of these before running the launcher for real (not
`--dry-run`). The launcher's own preflight step re-checks the model policy,
image pins and quota gate mechanically; it does **not** check the three
below, because they are branch/merge decisions outside its scope.

1. **RC2 is merged to `main`, pending user approval.**
   - As of this writing, `vic/integrate/assurance-rc2` (head `5282498`,
     covering F16 `noop-closure-cancelled`, D28 `status-clear-latency` and
     F17 `repair-attribution`) is **not** an ancestor of `origin/main`
     (`8cadce1`). Get the user's sign-off on the RC2 report, merge it to
     `main`, and only then treat this precondition as met.
   - RC1 (`47a3162`, this branch's own base) is a sound integration on its
     own (`RC1.md` takeaways), but it still carries F8's attribution gap and
     the N13 stray-dispatch residual that RC2's branches close. Launching
     phase 1 from RC1 alone under-tests C11 (memory safety) and the
     status-clear latency numbers RC1 flagged as a known gap.
2. **`sdo-*:v0.1.0` is rebuilt from the merged `main` (post-RC2), and no
   lane is using the old tags while it happens.**
   - Run: `uv run python -m benchmarks.sregym.assurance.rebuild_v010_images <post-RC2-main-sha>`
     from a checkout at that exact commit. It refuses (does not build) if
     any known kind cluster is locked or is running a pod built from one of
     the four `v0.1.0` images, or if the working tree is not at the commit
     you named — see its docstring and
     `tests/unit/benchmarks/sregym/assurance/test_rebuild_v010_images.py`.
   - It writes the rebuilt images' ids and repo digests to
     `.launch/v010_digests.json` (default location) for the run manifest.
     Fold that file's `images` block into the phase-1 manifest/run log before
     launching.
   - RC1.md's own decision log is explicit that the shared tags were **not**
     rebuilt during RC1 integration, and that skipping this step means the
     SDO arm runs without `950a4b7` (the closing-view-diff fix) and wedges
     after its first correct repair. Do not skip this.
3. **Preflight is green for every lane.**
   - `uv run python -m benchmarks.sregym.runner.preflight benchmarks/sregym/experiments/assurance/phase1/*.toml`
     must show every check `ok` except `codex-quota`, which is expected to
     read the pre-reset 90%+ figure until 2026-10-03 18:19 UTC.
   - The launcher (step 2 below) re-runs this per lane in enforcing mode and
     aborts the whole matrix on any failure, but running it by hand first is
     the fast way to catch a problem before the 2 h window opens.

## Commands

All commands run from the repository root, `uv run ...` per `CLAUDE.md`.

```bash
# 1. Confirm quota and preflight look right (repeatable, no side effects).
uv run python -m benchmarks.sregym.runner.preflight \
    benchmarks/sregym/experiments/assurance/phase1/*.toml

# 2. Rebuild v0.1.0 from the merged post-RC2 main (only once nothing is using it).
uv run python -m benchmarks.sregym.assurance.rebuild_v010_images <post-RC2-main-sha>

# 3. Launch the matrix. One command; resumable if interrupted (rerun the same
#    command — it reads .launch/state.json and continues, never restarts a
#    lane already done).
bash benchmarks/sregym/experiments/assurance/phase1/launch.sh

# 3'. Rehearse first with --dry-run: stubs cluster, quota and process calls,
#     touches no real cluster or quota, and exercises lane binding + preflight.
bash benchmarks/sregym/experiments/assurance/phase1/launch.sh --dry-run

# 4. After the matrix finishes (or is stopped), analyze.
uv run python -m benchmarks.sregym.assurance.phase1_analyze \
    --sdo third_party/sregym/logs/<pipeline-w0> third_party/sregym/logs/<pipeline-w1> \
          third_party/sregym/logs/<pipeline-w2> third_party/sregym/logs/<pipeline-w3> \
    --codex-stock third_party/sregym/logs/<w4> third_party/sregym/logs/<w5> \
    --codex-verify third_party/sregym/logs/<w6> third_party/sregym/logs/<w7> \
    --out benchmarks/sregym/experiments/assurance/phase1/report.md
```

The launcher's default lane binding comes from each config's own header
comment (`; lane assure-wN`), already fixed in `PLAN.md` D11:

| Lane | Config | Arm |
|---|---|---|
| `assure-w0`..`w3` | `sdo_codex_luna_assure_p1_{a,b,c,d}.toml` | SDO (4 pipelines, rotations A-D) |
| `assure-w4`, `w5` | `codex_luna_assure_p1_stock_{1,2}.toml` | Codex stock |
| `assure-w6`, `w7` | `codex_luna_verify_assure_p1_{1,2}.toml` | Codex + verify |

## Expected duration

**About 2 h wall clock** (`PLAN.md` (d)), staggered lane starts (default
120 s apart, `--stagger-seconds`), so the last lane (`assure-w7`) begins
about 14 minutes after the first.

- SDO lanes: 10 stages each (5 cold + 5 warm repeats); each stage runs a
  deploy-once, then inject/diagnose/mitigate/verify cycle.
- Codex lanes: 12-13 sequential attempts each.

## Monitoring while it runs

- **State:** `benchmarks/sregym/experiments/assurance/phase1/.launch/state.json`
  — one JSON document, `matrix_status` plus each lane's status
  (`pending`/`running`/`done`/`failed`/`aborted_budget`/`aborted_matrix_stop`)
  and its quota records.
- **Host load and disk:** `.launch/host_samples.jsonl`, one line every 30 s
  (`--sample-interval-seconds`): load averages and free bytes on the logs and
  Docker data disks. Watch for the same drop in free disk RC1.md flagged
  (PLAN.md budgets about 20 GB per 1+1 lane, 100+ GB free minimum).
- **Quota:** `.launch/quota_log.jsonl`, one line per completed lane run
  (`used_percent_before`/`after`, the attributed delta). Cross-check against
  `uv run python -m benchmarks.sregym.runner.preflight <any-config>` any
  time — it reads the same offline rate-limit snapshot.
- **Per-lane subprocess logs:** `.launch/logs/<lane>.log` (real run only;
  `--dry-run` starts nothing).

## Abort criteria (enforced by the launcher; also watch for them by hand)

- **Matrix-wide hard stop at `used_percent >= 97%`** (`PLAN.md` (d)
  `run.sh` `QUOTA-STOP`): the launcher terminates every running lane and
  sets `matrix_status = "stopped_quota"`. If you see this, the run is over;
  do not restart until the next quota window.
- **Matrix does not start** if `used_percent > 50%` at launch time
  (`matrix_status = "aborted_quota_start"`), or if **any** lane's own
  preflight fails (`matrix_status = "aborted_preflight"`) — the whole matrix
  aborts before any lane starts, by design (one bad lane must not silently
  run a partial comparison).
- **Per-lane abort** when a lane's own attributed quota spend exceeds 1.5x
  its budgeted share of its arm's `PLAN.md` (d) row (SDO ~1.55%/lane,
  threshold ~2.33%; Codex stock ~1.05%/lane, threshold ~1.58%; Codex +
  verify ~1.35%/lane, threshold ~2.03%) — that lane is terminated
  (`aborted_budget`) and the rest of the matrix continues. This usually
  means a retry storm or a stuck loop in that one lane; check its log before
  re-launching it alone.
- **Host disk below 100 GB free** is not auto-enforced mid-run today (only
  at preflight); if `host_samples.jsonl` shows it dropping toward that
  floor, stop the matrix by hand (`Ctrl-C` the launcher, or `kill` the PIDs
  in `.launch/logs/`) before it repeats RC1.md's "`/mnt/data` at 100%"
  incident.

## Analysis

`benchmarks.sregym.assurance.phase1_analyze` (step 4 above):

1. classifies every run with `run_validity` (valid / `agent_failure` /
   `invalid_infra`, with reasons);
2. runs `incident_cost` per SDO pipeline against both Codex arms, over the
   valid runs only, and includes each pipeline's rendered report;
3. computes C1-C11 against `PLAN.md` (a)'s pre-registered pass criteria,
   using a stratified bootstrap for ratios, Wilson for a proportion, and
   Newcombe for a difference of proportions, exactly as the plan specifies;
4. writes a report with a mandatory **Takeaways** section per claim.

**Known gaps in this analysis step** (flagged in the report itself, not
silently dropped):

- **C6 and C9** are no-LLM fastloop instruments (`PLAN.md` (e)), separate
  from the live matrix this tool reads; run them separately and score them
  with a dedicated tool.
- **C7's primary check (variant V1) and C10** are phase-2 only
  (`PLAN.md` (b)); phase 1's own analysis marks them `deferred`.
- **False closures (C4, C8)** need controller closure/receipt evidence
  beyond `incident_cost`'s `SdoStage`/`Verdict` model; check them by hand
  against `run_validity`'s helper-leftover and receipt checks before
  reporting either claim as a clean pass.
- **Decoy-citation counting (C11)** needs a diagnosis-text scan against the
  known decoy markers; this tool only computes C11's ratio-vs-verify half.

## After the matrix

- Commit the phase-1 experiment TOMLs' run log entry (per-lane `run_manifest.json`
  paths, the digests file from step 2, and the analysis report) under
  `benchmarks/sregym/experiments/assurance/` per the repo's "commit
  experiment configs" convention.
- If phase 1's headline claims look sound and quota allows
  (`used_percent <= 59%` after phase 1, `PLAN.md` (d) "Phase-2 start"),
  proceed to phase 2; otherwise run only the phase-2 delta (S4, V1, K3).
