# Luna reuse comparison: decisions log

This log covers SDO (`sdo_codex_luna_reuse.toml`, two rounds) against stock Codex (`codex_luna_baseline.toml`, two memoryless attempts). Both use gpt-6-luna on `missing_configmap_hotel_reservation`.

## Measurement

- **Primary metric: `mitigation_submitted_at - fault_injected_at`.** Both are conductor wall-clock epochs. The first is taken right after `problem.inject_fault()`. The second is taken when the agent's mitigation `POST /submit` reaches the API, before in-API retries or oracles run.
  - Alternatives: SREGym `TTM`, or SDO's receipt `incident_resolution_seconds`.
  - Why: `TTM` ends after the diagnosis judge and the mitigation oracle, which is grader time rather than agent time. The receipt metric starts at SDO's own detection, so it hides detection latency and exists for one arm only.
  - `TTM` and `TTL` are still reported.
- **Detection latency counts against SDO.** Codex is told about the incident at launch. SDO must detect it through its controller. Likewise, Codex container and CLI startup after injection counts against Codex.
- **The fault is injected after SDO setup.** This uses the new opt-in conductor flag `defer_fault_injection` and `POST /inject_fault`. The SDO driver runs lifecycle, installs the controller, and waits for the first all-clear evaluation from the current controller Job, then injects.
  - Alternative: stock order, where the fault is injected before the agent launches. That puts cold lifecycle (~220s) and controller install (~120s) inside SDO's TTM, and it runs the health judge on a broken app. Neither matches how SDO operates.
  - Stock agents keep the original flow and TTM origin.
  - SDO's one-time setup is reported separately (`fault_injection_deferred_seconds` and the receipt's driver and gate timings).
- **SDO diagnosis submission no longer waits for grading.** Before, `adapter/submission.py` blocked until the diagnosis judge finished before the responder could repair. Codex's curl returns on acknowledgement. Both arms now behave like the stock agent.
- **Tokens.**
  - SDO incident tokens are responder plus reflection. A responder-only figure is reported for an apples-to-apples comparison, and lifecycle tokens are reported separately.
  - Codex tokens come from its `codex exec --json` usage and session files.

## Environment

- **Judge: Codex CLI `gpt-6-astra` with `xhigh` reasoning (`codex-gpt-6-astra`),** identical for both arms.
  - This was the user's choice over Vertex Gemini.
  - SREGym had no `codex-*` route, so `llm_backend/codex_cli_backend.py` was added.
  - gpt-6-astra has the top priority in the local Codex model list.
- **Reasoning effort: medium on both arms.** SDO's responder and reflection are hardcoded to medium. The baseline uses Codex's default for gpt-6-luna, which is medium, because only `auth.json` is copied into its container.
  - SDO lifecycle uses high and medium, but it is one-time and reported separately.
- **Codex CLI 0.157.1 in both arms.** SDO images were bumped from 0.144.0-alpha.4, which rejects gpt-6-luna for ChatGPT logins. The baseline installs npm `latest`, which is 0.157.1 on the run date.
- **Same cluster: kind `luna-w0`,** using a private prefix so shared clusters are untouched.
  - Calico with enforced NetworkPolicy for both arms. SDO requires it; the baseline was given the same CNI and preloaded images through env in the run wrapper.
  - `worker_cpu_limit = 3`, `deploy_from_source = true`, `preserve_infrastructure = true`, reused across runs.
- **Run order: Codex A1, then SDO R1 and R2, then Codex A2.** This interleaves the arms to spread machine-load drift. Runs never overlap, and the load average is sampled every 30s.
- **Image builds use the `sdo-example` buildx builder.** The default builder's cache is corrupt ("parent snapshot does not exist"). Shared builder state was not pruned.
- **SDO round 1 is cold.** A new pipeline directory gives it a fresh application workspace copied from SREGym-applications, which has no `.sdo/`. Round 2 chains that workspace.
- **Hotel source build pin (SREGym-applications `d1a7e02`).** `go mod vendor` in the hotel Dockerfile resolved zerolog v1.35.1, which needs Go 1.21, while the image uses Go 1.17. Every source deploy failed.
  - The fix pins zerolog to v1.20.0, the version in the last good image. Both arms build from the same Dockerfile.
  - This happened on Codex A1's first deploy attempt, before any fault. The conductor's built-in retry succeeded after the fix, so A1's incident window is unaffected.

## Fairness caveats (not removed)

- **Kubernetes access path differs.**
  - Codex reaches the API through SREGym's filtering proxy (`sregym/service/k8s_proxy.py`). The proxy buffers whole responses and does not support connection upgrades, so `kubectl exec`, `attach`, and `port-forward` fail. A1 evidence: `websocket: close 1006 (abnormal closure)`.
  - SDO's responder uses in-cluster RBAC (`controller/runtime/deploy/rbac.yaml`, role `sdo-responder`). That role is namespace-scoped and grants no `pods/exec`, so neither arm can exec.
  - The asymmetry is mostly in breadth: Codex is cluster-wide but has no exec; SDO is namespace-only with no exec. The stock harness was left unchanged so A2 runs exactly like A1.
- **Infrastructure cleanup before grading.** The SDO adapter deletes its own controller and responder Jobs before the oracle runs. Agent-created repair Jobs are not deleted in either arm.
  - A1 failed the mitigation oracle only because Codex left its own repair Job's `Succeeded` pod. It had created that Job after `kubectl exec` failed through the proxy.
  - The same leftover would fail SDO too.
- **Success is compared before time.** A run that fails an oracle is reported as "failed after X s". It is not compared as a time-to-fix.

## SDO behaviour fixes made during the experiment

- **ExternalName Services no longer need endpoints in the health objective (`9da14b3`).** In SDO attempt 1 (`20260927_090342_pipeline_sdo-codex-luna-reuse`), the adapter objective required hotel's ExternalName `jaeger` Service to expose ready endpoints. The health judge encoded that, and the controller baseline never cleared, so the gate correctly refused to inject.
  - Alternatives:
    - Patch the generated detector by hand. This hides the real input bug.
    - Validate lifecycle against the live cluster. That changes lifecycle architecture.
  - Chosen: fix the objective the adapter feeds to lifecycle.
  - The aborted attempt spent about 25 minutes and about 1.65M lifecycle input tokens. It is excluded from the results table but kept as evidence.
- **Wildcard conductor host routes through the relay (`a8967a4`).** In SDO attempt 2 (`20260927_093811_pipeline_sdo-codex-luna-reuse`), SREGym's default `API_HOSTNAME=0.0.0.0` was not treated as local. No submission bridge was deployed, and the responder's diagnosis POST to `0.0.0.0` was refused inside the pod.
  - Following the "do not repair before diagnosis is acknowledged" instruction, the responder then reported `failed`. Detectors never cleared, and the controller crash-looped on "detector review required".
  - Fix: treat `0.0.0.0` as loopback in the driver and the relay.
- **Attempt 3 reuses attempt 2's validated lifecycle but still starts the incident cold.** Attempt 2's workspace after lifecycle has only the lifecycle commit, an empty `outcomes.jsonl`, and no incident playbooks or detectors. It was copied before any incident write-back and seeds attempt 3's stage 0 through `SREGYM_APP_WORKSPACE_SEED_DIR`. Stage 1 still chains stage 0's workspace.
  - Alternative: a third cold lifecycle, costing about 25 minutes and about 1.7M tokens.
  - Lifecycle cost is outside the incident metric either way. It is reported from attempt 2's `sdo_turn_usage.jsonl`, saved in the scratch notes, and from attempt 2's timestamps.
- **Reflection uses a strict schema (`e45cd52`).** In SDO attempt 3 (`20260927_102206_pipeline_sdo-codex-luna-reuse`), round 1 passed both oracles: diagnosis POST at +20s, mitigation POST at about +101s, TTM 100.9s.
  - The same-session reflection then failed every retry with Codex `invalid_json_schema`, because `no_change_reason` was optional. Round 2 could not start and would have had no learned memory.
  - Fix: every property required, `no_change_reason` nullable. Verified live against gpt-6-luna.
  - Attempt 3's round-1 incident numbers are kept as an extra data point. The whole pipeline was rerun as attempt 4 so round 2 chains from a round 1 whose reflection completed.
- **Lifecycle survives SDO-validated source repairs (`9cc6983`).** In attempt 4 (`20260927_104328_pipeline_sdo-codex-luna-reuse`), round 1 passed both oracles (primary 102.5s) and reflection completed. Round 1's validated outcome also committed a source repair, `kubernetes/geo/mongo-geo-script-configmap.yaml` (broker commit `505fbc3`).
  - Round 2 then treated the whole lifecycle as stale ("deployer topology_fingerprint does not match tracked source") and started a cold deployer and health-judge rerun. That costs about 25 minutes and about 1.7M tokens, and it defeats the memory-reuse round.
  - Fix: reuse accepts source drift only when every source-changing commit since the handoff is a broker-validated SDO commit, the deployer assessment still holds at its recorded commit, and the health judge's derived input is unchanged. Operator changes and changes to judged topology still force a new lifecycle.
  - Verified on a copy of round 1's workspace against the live round-2 inventory: reuse is accepted, and the container validator re-attests the changed diagnostics.
  - Alternative: let round 2 finish with a re-authored lifecycle. Rejected, because the incident window would still be valid but round 2 would no longer test reuse, and it would spend lifecycle tokens for nothing.
  - The round-2 run was stopped during lifecycle, before any fault injection, and resumed with `run_sregym.sh <pipeline> --stage 1`. The runner renames the aborted stage to `stage_1_reused-incident.20260927_112225` and re-chains round 1's unchanged workspace (HEAD `23457b9`).
  - **Images were not rebuilt.** Lifecycle reuse runs host-side in the driver. The in-image change is only the broker's identity strings, moved into shared constants with identical values. This keeps images fixed, as instructed.

## Results

Primary is `mitigation_submitted_at - fault_injected_at`. Load is the host 1-minute load average from the nearest 30s sample.

| Run | Log dir | Diagnosis / Mitigation | Primary | Diagnosis POST | TTL / TTM | Incident tokens (input / cached / output) | Load at injection → mitigation |
|---|---|---|---|---|---|---|---|
| Codex A1 | `20260927_084657_codex` | pass / **fail** | failed after 167.6s | +70.7s | 102.8 / 167.9s | 861,344 / 811,776 / 4,374 | 14.1 → 7.0 |
| SDO R1 | `20260927_104328_pipeline_sdo-codex-luna-reuse/stage_0_first-incident` | pass / pass | 102.5s | +33.4s | 59.0 / 102.7s | responder 748,868 / 696,064 / 5,092; reflection 1,804,250 / 1,671,936 / 13,432 | 6.2 → 4.4 |
| SDO R2 | `.../stage_1_reused-incident` | pass / pass | 93.0s | +22.9s | 52.6 / 93.2s | responder 619,014 / 572,928 / 4,100; reflection 954,369 / 851,968 / 8,012 | 7.8 → 6.5 |
| Codex A2 | `20260927_113953_codex` | pass / pass | 167.1s | +52.4s | 77.2 / 167.6s | 438,072 / 397,312 / 2,198 | 8.5 → 6.1 |

- **SDO one-time costs, excluded from primary.**
  - Lifecycle (attempt 2, reused as the seed): 5 host turns, 1,746,095 input (1,517,312 cached), 20,325 output tokens, 757s of model-turn time, about 24 min wall clock.
  - Controller baseline gate before each injection: 116.0s plus a 6.5s injection request in both rounds.
  - R2 lifecycle reuse including container re-validation of the changed diagnostics: 205s. R1 took 1.5s because of the attested seed.
  - Post-recovery reflection, learning, and receipt: R1 342s, R2 273s.
  - Worker wall clock: R1 821s, R2 923s, A1 739s (includes a hotel build retry), A2 334s.
- **Load confound check.** No SDO incident window ran near 2x A1's load; both were at or below it. No incident was rerun for load.
- **Round-2 reuse.**
  - `lifecycle_reused=true`. Memory reuse took the warm path with an exact fingerprint match, 1 candidate, and 2 applied playbooks.
  - The learned incident detector fired alongside the health detector.
  - Compared with R1, the repair began 17s sooner (+26s vs +43s after injection). Both rounds then spent about 60-67s from repair apply to mitigation submit waiting for Kubernetes recovery (kubelet mount retry, mongo restart, readiness) and running playbook verification.

## Result caveats

- **Detection precedes the clock.** SDO detected the fault about 5s before `fault_injected_at` in both rounds: R1 detection 10:48:13 vs clock 10:48:18; R2 11:30:36 vs 11:30:41.
  - The conductor stamps `fault_injected_at` after `problem.inject_fault()` returns, which takes about 6.5s. Codex starts after that stamp, so it cannot benefit.
  - Measured from the start of the injection request instead, SDO's primary is about 109s (R1) and about 99s (R2).
- **Tokens: SDO does not beat Codex.**
  - SDO's responder alone used more input and output than A2: R2 619k vs 438k input, 4.1k vs 2.2k output. Uncached input is similar: R2 46.1k vs A2 40.8k and A1 49.6k. The gap is mostly cached context resent across more model requests, plus the structured IncidentResult receipts.
  - Reflection costs 1.5-2.4x the responder and is paid on every incident.
  - Responder Codex transcripts are not persisted: the pod is deleted before grading and there is no session PVC. Per-request attribution was therefore not possible, and no token change was made without evidence.
- **Aborted SDO round 2 before the fix.** Kept as `stage_1_reused-incident.20260927_112225`. It had started a cold lifecycle rerun and was stopped before fault injection. No incident data.
