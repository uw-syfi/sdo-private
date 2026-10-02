# Mixed stream (single faults plus composites): decisions log

## Tier 0: composite C1 as one conductor pipeline stage

Branch `vic/exp/mixed-stream-smoke` (from `main` `d6efc100`), worktree `/mnt/data/shli/sdo-worktrees/mixed-smoke`. Goal: show that a composite problem runs end to end as a stage of a persistent-controller pipeline through the full conductor (cold SDO controller, Codex gpt-6-luna, judge `codex-gpt-6-luna` xhigh, strict receipt, official oracle, judge-free TTD/TTM).

### Decisions

- **Worktree and submodule.** New worktree from `main`; `third_party/sregym` initialised at the recorded pin `8035c290` (SREGym `sdo` branch) with `--reference` to the shared clone, `SREGym-applications` at `d1a7e02`. Alternative: reuse the main checkout's submodule. Rejected: the main checkout must stay untouched.
- **Images: private tag `mx1`**, built from this worktree (`SDO_IMAGE_TAG=mx1 BUILDX_BUILDER=sdo-example`). Alternatives: reuse `nf5` (built 16 h ago from the pre-merge composite-stream branch, so not provably equal to `main`), or `v0.1.0` (never retag).
- **Cluster: prefix `smoke-w`, `SREGYM_WORKER_ID_OFFSET=40`** gives cluster `smoke-w40`, API port 8040, MCP port 9994; no existing cluster or listening port uses these.
- **Settings.** All-on SDO from `composite-stream-decisions.md` expressed as `agent_config` keys: `late_findings = "pull"`, `max_follow_ups = 3`, `follow_up_cooldown_seconds = 30`, `reflection_guidance = "generalize"`, `reflection_session = "fresh"`, `healthy_baseline = true`. Everything else is copied from `sdo_codex_luna_stream_pilot.toml` (reasoning medium, 1 control plane + 1 worker, worker_cpu_limit 3, deploy from source, strict receipts). Config: `benchmarks/sregym/experiments/mixed-stream/tier0_smoke.toml`.
- **One stage first.** A second (repeat) stage only if stage 1 is clean and quota allows. Codex weekly quota at start: 92 % used (stop rule 97 %).
