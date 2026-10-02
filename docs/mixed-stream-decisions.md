# Mixed stream (single faults plus composites): decisions log

## Tier 0: composite C1 as one conductor pipeline stage

Branch `vic/exp/mixed-stream-smoke` (from `main` `d6efc100`), worktree `/mnt/data/shli/sdo-worktrees/mixed-smoke`. Goal: show that a composite problem runs end to end as a stage of a persistent-controller pipeline through the full conductor (cold SDO controller, Codex gpt-6-luna, judge `codex-gpt-6-luna` xhigh, strict receipt, official oracle, judge-free TTD/TTM).

### Decisions

- **Worktree and submodule.** New worktree from `main`; `third_party/sregym` initialised at the recorded pin `8035c290` (SREGym `sdo` branch) with `--reference` to the shared clone, `SREGym-applications` at `d1a7e02`. Alternative: reuse the main checkout's submodule. Rejected: the main checkout must stay untouched.
- **Images: private tag `mx1`**, built from this worktree (`SDO_IMAGE_TAG=mx1 BUILDX_BUILDER=sdo-example`). Alternatives: reuse `nf5` (built 16 h ago from the pre-merge composite-stream branch, so not provably equal to `main`), or `v0.1.0` (never retag).
- **Cluster: prefix `smoke-w`, `SREGYM_WORKER_ID_OFFSET=40`** gives cluster `smoke-w40`, API port 8040, MCP port 9994; no existing cluster or listening port uses these.
- **Settings.** All-on SDO from `composite-stream-decisions.md` expressed as `agent_config` keys: `late_findings = "pull"`, `max_follow_ups = 3`, `follow_up_cooldown_seconds = 30`, `reflection_guidance = "generalize"`, `reflection_session = "fresh"`, `healthy_baseline = true`. Everything else is copied from `sdo_codex_luna_stream_pilot.toml` (reasoning medium, 1 control plane + 1 worker, worker_cpu_limit 3, deploy from source, strict receipts). Config: `benchmarks/sregym/experiments/mixed-stream/tier0_smoke.toml`.
- **One stage first.** A second (repeat) stage only if stage 1 is clean and quota allows. Codex weekly quota at start: 92 % used (stop rule 97 %).

### Tier 0 result (2026-10-02, run `20261002_044638_pipeline_sdo-codex-luna-mixed-smoke`, cluster `smoke-w40`, deleted afterwards)

**The composite runs as a conductor pipeline stage with no harness change, but the incident was not solved.** The conductor accepted `composite3_hotel_geo_rate_recommendation`, the cold lifecycle and persistent controller worked, the deferred injection gate injected all three faults, the strict receipt is valid (`completed`, `acknowledged`, `cleaned`, validator Job with NetworkPolicy canaries passed), and both judges ran (luna xhigh). No test-first harness fix was needed, so there is no code change in this branch.

| item | value |
| --- | --- |
| faults | 3 (geo readiness probe port, `mongo-rate-script` ConfigMap for mongodb-rate, NetworkPolicy isolating `recommendation`) |
| SDO repaired | geo readiness and the ConfigMap (2 of 3); the NetworkPolicy on `recommendation` was never found |
| Official oracles | `ReadinessProbeMitigationOracle` pass, `MitigationOracle` pass, `NetworkPolicyMitigationOracle` **fail**; `Mitigation.success` False (66.7 %) |
| Diagnosis (judge) | False, 2 of 3 faults (66.7 %); the diagnosis text lists only the readiness and ConfigMap causes |
| Controller view | every health detector `clear` at 05:11:22Z; healthy verify burst passed 5/5 for hotel-login, hotel-recommendations, hotel-search; `no_incident_detector_fired` true |
| Probes versus oracle | the SDO health detectors and probes agreed the app was healthy; only the official NetworkPolicy oracle saw the remaining fault (same disagreement as the 2 of 40 composite-stream oracle misses, but here it is the first and only composite) |
| Pull-before-act | on (2 non-empty pulls, `late_finding_consumed` true); follow-ups did not trigger because no finding stayed active |
| Time (judge excluded) | diagnosis submitted 22.3 s after `fault_injected_at`; mitigation submitted 157.5 s after it, of which the 71 s diagnosis-grading wait (05:07:48 to 05:08:59) sits inside the window, so about 86 s judge-free; receipt `incident_resolution_seconds` 250.9 (detected to verified health, started at detection, before the injection timestamp); responder 237.7 s |
| Pre-injection cost (excluded from TTM) | inventory and lifecycle 678 s, controller install 12 s, healthy baseline 5 s, fault gate 32 s; fault_injection_deferred_seconds 724 |
| Tokens (this incident) | responder 0.52M input (0.475M cached) + 5.9k output; reflection 1.04M input (0.98M cached) + 15k output, 28 requests; lifecycle tokens not extracted |
| Wall clock | 1340.8 s for the pipeline (about 33 min including cluster up, under the 45 min budget); no infra stall |
| Learning | reflection added `missing_required_configmap` and `readiness_port_mismatch` incident detectors (accepted); nothing for the NetworkPolicy |
| Quota | 92 % used (the newest rollouts, including this run's, all read 92 %); stop rule 97 % not reached |

Infra: none (no kind, Calico, image, controller-install or health-timeout failure). The `codex-quota` preflight line warned that its snapshot was stale; Tier 1's stale `SDO_VALIDATOR_IMAGE` problem does not apply here because the config pins `mx1` for the validator and the validator canaries passed.

Takeaways:

1. The mixed stream is feasible in the conductor path: composite IDs need no harness change (receipt, judges, oracle, deferred injection, persistent controller all work). Confidence: high for one stage; no repeat stage was run.
2. A cold first composite containing a NetworkPolicy fault is not solved by the health detectors, and this is the exact fault class the fairness audit removed hard-coded knowledge for (`fairness-DECISIONS.md` item 2). The user-facing traffic checks stay green with `recommendation` isolated, so SDO declares health restored while the official oracle disagrees. Confidence: medium (n=1; the earlier fast-loop stream resolved the cold C1 3/3, so part of the difference may be run-to-run luck or the stricter conductor grading versus read-only probes). Implication: in the full-conductor mixed stream, composites that contain the NetworkPolicy fault will score as unsolved on first occurrence unless a detector for it is learned, so score them separately and expect the learning curve to show up there. Next step: rerun the cold C1 once or twice to see whether the miss is systematic, and look at whether the controller's `traffic-health` probes ever hit `recommendation`.
3. The second (repeat) stage was not run: stage 1 was not clean, which was the condition for running it.
