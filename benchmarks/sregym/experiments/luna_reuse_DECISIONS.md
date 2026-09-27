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

## v2

- **Codex A3 is a third baseline sample, because A1 and A2 tokens varied 2x (861K vs 438K input).** Run on branch `vic/exp/luna-reuse-v2`.
  - The baseline path is unchanged since A2 (`9b990af`): no diff to `benchmarks/sregym/runner`, `run.py`, `scripts/run_sregym.sh`, the `third_party/sregym` pointer (`f697ecd5`), `registry.yaml`, or `codex_luna_baseline.toml`. The v2 edits touch only SDO responder, reflection, lifecycle, controller and adapter code, which the stock Codex agent does not use.
  - Same wrapper (`run.sh`: Calico, enforced NetworkPolicy, preloaded images), cluster `luna-w0`, and judge `codex-gpt-6-astra`/xhigh. `SREGYM_APP_WORKSPACE_SEED_DIR` was unset. Nothing else was running on the host.
- **A3 result (`20260927_120459_codex`): diagnosis FAIL, mitigation pass. Reported as failed; its time is not compared as a time-to-fix.**
  - Timing: injected 12:07:44.9. Diagnosis POST at +57.5s, mitigation POST at +265.1s. TTL 81.1s, TTM 265.6s. Worker time 426s.
  - Tokens: 886,324 input (827,136 cached), 5,288 output (2,469 reasoning), 21 model requests, 1 exec turn.
  - Load, start → end: 4.25 → 4.37. The 1-minute load was 13.85 at 12:07:28 and 11.62 at 12:07:58 around injection, and 6.09 at mitigation. This is comparable to A1 (14.1 → 7.0).
  - Why diagnosis failed: Codex correctly named the missing `mongo-geo-script` ConfigMap. It also claimed a second root cause, an "admin privilege revocation" on the geo and rate MongoDBs, inferred from the app's mounted recovery scripts. All three xhigh judge votes rejected the diagnosis. This is agent behaviour, not a harness fault.
  - Mitigation: Codex recreated the ConfigMap and ran ad-hoc `fix-geo-role`/`fix-rate-role` pods to re-grant roles. It deleted those pods before submitting, so unlike A1 no leftover pod failed the oracle.
- **Codex baseline variance across three samples.**
  - Input tokens: 861K, 438K, 886K (median 861K). Output tokens: 4.4K, 2.2K, 5.3K. Model requests: 21, 12, 21.
  - Mitigation submit at 167.6s, 167.1s, 265.1s.
  - Only A2 passed both oracles. A2 is the low-token outlier, not A1.
- **Cluster handoff.** `luna-w0` is kept: 4 nodes Ready, the `hotel-reservation` namespace was removed by the stock cleanup, and no pods are unhealthy. It is ready for the v2 SDO pipeline.
- **This DECISIONS edit is left uncommitted.** Another agent is committing on `vic/exp/luna-reuse-v2`, and this task was limited to editing this file.

### v2 warm-path efficiency fixes (SDO code)

Evidence: R2 (`20260927_104328_pipeline_sdo-codex-luna-reuse/stage_1_reused-incident`) had an exact-fingerprint match on a validated incident detector plus a learned playbook. Its responder still used 619K input tokens and about 20 model requests, with the same 70 s diagnosis-to-mitigation window as cold. Its reflection used 954K tokens and 273 s, rewrote the detector's provenance, and forced validator Jobs plus a controller rollout.

- **Warm fast mode: definition of "exact match".** It is computed in `sdo/operational_memory/warm_path.py` from data the responder already has. `IncidentRequest` is unchanged. An incident is warm when both hold:
  - an active finding comes from a detector that the worktree's `.sdo/diagnostics/manifest.yaml` registers as `class: incident` and `owner: responder`, and that finding lists a playbook;
  - some `relevant_outcomes` entry has `match_reason=exact-fingerprint`.
  - In R2 the exact match came from the health-objective finding: the prior outcome predates the incident detector, and `PriorOutcomeEvidence` does not say which finding matched. So this rule does not require the matching fingerprint to be the incident detector's. Requiring that would have left the v2 rerun's round 2 cold.
  - `SurfacedPlaybook` has no match reasons, and adding them would change the contract, so the rule uses `relevant_outcomes` instead.
- **Warm prompt.**
  - Content: it inlines each warm finding's playbook (capped at 8K chars) and its `scripts/*.sh` (capped at 8K total), and states that the validated incident detector's evidence already establishes the preconditions.
  - Prescribed order: one combined sanity check, which replaces the playbook's own diagnosis steps; the diagnosis; the playbook repair; the playbook's verification once; then mitigation.
  - It also says to delete or rollout-restart pods stuck on a restored ConfigMap or Secret mount. The learned playbook will not say this until it is re-learned.
  - Fallback: a full investigation, only on a contradicting sanity check, a failed repair, or a failed verification.
  - If the playbook file is missing or the manifest is unreadable, the prompt falls back to cold. The cold prompt text is unchanged.
- **Context trim.**
  - `detector_history` is removed from the prompt's request JSON and replaced by one line per detector: its latest evaluation, plus the latest firing evaluation when that is older.
  - `goal.md` is inlined when it is at most 4K chars. The hotel goal is about 1K.
  - This applies on both paths. The full history stays in the request, ledger and outcome.
- **Reflection guidance.** Playbooks must now include:
  - concrete, copy-pasteable verification commands, including a representative request command when the objective needs one;
  - multi-step commands in `.sdo/playbooks/<playbook>/scripts/*.sh`, the layout the validator already `bash -n` checks and `AppliedPlaybook.scripts` reports;
  - a restart of stuck pods after restoring a mount source;
  - no re-diagnosis that the incident detector already establishes.
- **Provenance.**
  - The prompt now sets `originatingIncident`/`originatingCommit` only for a new detector.
  - `MemoryValidator` rejects a responder proposal that changes those fields for an existing incident detector, in the manifest or in the `Originating*` string literals of its Go `Spec()`. It checks the Go literals directly so the rejection happens before any validator Job.
  - Health-judge edits are not covered, because the lifecycle legitimately rewrites health detector provenance.
- **Deterministic no-op reflection.** The broker skips the LLM turn when all of these hold:
  - the outcome is `success` and the controller verified health;
  - the result completed with confirmed root causes, every repair action succeeded, and every verification passed;
  - the incident is warm under the same rule as above, and the responder applied the warm finding's playbook;
  - that incident detector's post-response evaluation is `clear`.
  
  In that case it records an empty attributable reflection commit (no validator run, no detector change, no rollout) with `learning_decision=no_change`. The reason goes in both `reflection_no_change_reason` and the new optional ledger field `reflection_skipped_reason`.
  - Why an optional field rather than `no_change_reason` alone: analysis must distinguish "the LLM chose no change" from "no LLM turn ran".
  - The strict receipt keeps `same_session_reflection=true`, because the contract requires a reflection commit. It adds `reflection_skipped_reason` and `incident_detector_states`.
- **"Incident detector cleared" needed controller evidence.** When health detectors exist, the closure's `final_detector_states` holds only health detectors.
  - Added `IncidentClosure.incident_detector_states` in Go and `BrokerClosure.incident_detector_states` in Python, both optional. The value is each non-health finding detector's latest evaluation since responder completion.
  - It is learning evidence only. Closure gating and `final_detector_states` are unchanged, so outcome classification cannot shift.
  - If the detector did not evaluate after the response, the state is absent and full reflection runs.
- **Also relaxed the broker's "confirmed success must include a detector update" rule.**
  - It now applies only when no registered responder-owned incident detector raised a finding.
  - Otherwise, a playbook-only improvement for an already-learned fault was rejected. That pushed the model to make cosmetic detector edits, which is how R2's provenance rewrite happened, and each edit cost a validator run plus a rollout.
  - The two tests that pin the rule now use a closure from an unlearned detector.
- **Responder transcripts.** `vic/perf/reflection-tokens` already exports `/workspace/.sdo-runtime/codex/sessions`. Responder Jobs already run with `CODEX_HOME=/workspace/.sdo-runtime/codex` on the same PVC, so their rollouts are exported, and a test now pins this.
  - Added `shell_command_lines` (commands in order, each capped at 2K chars) to every usage-log record, so the responder's commands are in `sdo_runtime/usage/responder-turns.jsonl` without parsing rollouts.
  - The commands are not put in the ledger or receipt, because that would bloat both. The exported artifacts are the intended place.
- **Not done.**
  - No contract changes to `IncidentRequest`, `SurfacedPlaybook` or `PriorOutcomeEvidence`.
  - The existing learned playbook in R1/R2 workspaces was not edited; the rerun re-learns from stage 0.
  - No change to the SREGym responder instructions in the adapter.
- **Validation and images.**
  - Checks: `format_code.sh` and `check_errors.sh` (ruff and tach) are clean. `go test ./...` passes in `controller/runtime`, `controller/sdk` and `controller/core`. `pytest tests/unit/sdo tests/unit/libs/agent_cli tests/unit/benchmarks/sregym/adapter tests/unit/controller`: 332 passed, 2 skipped.
  - Images: `BUILDX_BUILDER=sdo-example bash scripts/build_sdo_images.sh` succeeded at code head `48f7461`. It produced `sdo-detector-validator`, `sdo-controller`, `sdo-responder` and `sdo-sregym-responder`, all tagged `v0.1.0`.
