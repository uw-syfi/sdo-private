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

### v2 SDO pipeline run (`20260927_122641_pipeline_sdo-codex-luna-reuse`)

- **Setup.** Code head `ae27921`. Images were built 12:23–12:25 from `48f7461`, which is code-identical to head (`48f7461..ae27921` is docs-only); verified with `docker image inspect`.
  - Same TOML (`sdo_codex_luna_reuse.toml`, unchanged since `9b990af`), wrapper `run.sh`, cluster `luna-w0`, and judge `codex-gpt-6-astra`/xhigh.
  - Round 1 was seeded from the same clean post-lifecycle workspace as v1 (`SREGYM_APP_WORKSPACE_SEED_DIR=<scratch>/seed/lifecycle_workspace`: lifecycle commit only, no incident memory). Round 2 chained round 1's workspace.
- **One-time lifecycle cost.** The seeded lifecycle was reused in both rounds.
  - The new validator identity forced a container revalidation plus an attestation commit, with no LLM turns and 0 tokens (no host `sdo_turn_usage.jsonl` was written). It took 206.6s in R1 and 214.0s in R2; R2 also revalidated R1's new incident detector.
  - A full lifecycle rerun was not needed: the seeded health detector passed the ExternalName-hardened validator.
- **R1 (cold): pass/pass.**
  - Primary 91.7s. Diagnosis POST at +22.2s, TTL 48.5s, TTM 91.9s.
  - Gate: 123.2s baseline wait plus a 6.8s injection request. Load 10.6 at injection, 6.0 at mitigation.
  - Responder: 666,199 input (618,496 cached), 5,240 output, 25 requests, 140s.
  - Reflection: 3 attempts (2 fresh retries), 1,940,917 input (1,744,640 cached), 33,250 output, 32 requests. Post-recovery time 752s. Stage wall time 1,428s.
- **R1 reflection needed 3 attempts.**
  - Attempt 1 (same-session resume): 1,455,194 input, 15 requests, 312s. Rejected with `playbook is missing from index`: the new playbook was not linked from `.sdo/playbooks/README.md`.
  - Attempt 2 (fresh): 298,493 input, 10 requests, 175s. Rejected with `playbook must use a role placeholder`: `MemoryValidator` requires `<[A-Z][A-Z0-9_]+>`, and the playbook used lowercase `<namespace>`-style placeholders.
  - Attempt 3 (fresh): 187,230 input, 7 requests, 100s. Accepted.
  - Neither the v1 nor the v2 reflection prompt states these two validator rules; v1 happened to satisfy them on the first try.
  - **Not fixed mid-pipeline.** It did not block the run and does not affect the primary metric, and changing the images between rounds would confound R2. Recommended follow-up: state both rules (index link, UPPER_CASE placeholders) in `_PLAYBOOK_RULES`. That would have saved about 486K input tokens and 275s here.
- **R1 learned memory meets the executable-verification requirement.**
  - Incident detector `missing-geo-mongo-init-configmap`.
  - Playbook `missing-geo-mongo-init-configmap` with `scripts/repair.sh` (apply the manifest, rollout restart, rollout status) and `scripts/verify.sh`. The verify script checks replica agreement, that the Service endpoint IP belongs to a ready pod, and a frontend `/hotels` request expecting HTTP 200 and a GeoJSON FeatureCollection.
- **R2 (warm): pass/pass.**
  - Primary 51.7s. Diagnosis POST at +16.1s, TTL 36.2s, TTM 51.9s.
  - Gate: 122.2s plus 6.5s. Load 9.6 at injection, 8.9 at mitigation.
  - Responder: 458,095 input (409,856 cached), 3,308 output, 20 requests, 131s (it keeps verifying after the mitigation POST).
  - Reflection: 1 attempt, 568,876 input (478,976 cached), 4,916 output, 3 requests, 36s. The commit was empty (the LLM chose no change), with `validator_skipped_reason=unchanged-diagnostics` and no controller update. Post-recovery time 43s. Stage wall time 714s.
- **R2 did not take the warm fast mode, and its reflection was not the deterministic no-op.**
  - `memory_reuse.warm_path=true` in the receipt is the retrieval flag (one exact-fingerprint prior outcome). It is not the fast-mode prompt. The exported rollout shows the cold prompt: it has the compacted evidence and inlined `goal.md`, but no inlined playbook or warm instructions.
  - Cause: at dispatch the learned incident detector was `clear`. Its last evaluation was at 13:00:02.4. The health detector fired at 03.28, 03.68 and 04.08, and dispatch followed at 04.49.
    - The v2-learned detector watches only Deployment and ConfigMap, and it chose `Persistence{Firing: 2}`. It never accumulated two active evaluations before the controller dispatched on the health batch.
    - The health detector watches Pods, Endpoints and Events, so it re-evaluated on every pod event.
    - v1's learned detector used `Firing: 1` and fired in the same evaluation batch as health.
  - With no active incident-detector finding, the warm rule and the no-op rule both declined, which is correct by their definitions. `incident_detector_states=[]`, because only detectors that raised a finding are listed.
  - Unexplained detail: no incident-detector evaluation was recorded after the ConfigMap delete, even though it watches ConfigMaps. Controller logs are not exported and the pod was cleaned up, so I could not tell whether the watch event was delayed or coalesced.
  - **Not fixed and not rerun.** R2 passed both oracles, so the run was not blocked. The correct fix is a controller-runtime change with closure implications: at dispatch, evaluate registered responder-owned incident detectors on the dispatch snapshot and attach their raw active findings as enrichment. That needs a design decision about `incidentFindingKeys` and closure gating, which is too invasive for a mid-experiment patch on a branch another agent is editing.
  - Cheaper mitigations for the follow-up:
    - have reflection guidance require that incident detectors use `Firing: 1` and watch the kinds on which the fault manifests (Pods and Events);
    - or have the warm rule accept an exact-fingerprint prior outcome whose incident detector is registered, even if it has not fired yet.
- **v1 vs v2 vs Codex.**
  - Primary: v2 R1 91.7s vs v1 R1 102.5s; v2 R2 51.7s vs v1 R2 93.0s. Codex mitigation POSTs were 167.6s, 167.1s and 265.1s; only A2 passed both oracles.
  - Responder input: v2 666K and 458K vs v1 749K and 619K vs Codex 861K, 438K and 886K.
  - Reflection input: v2 1.94M and 569K vs v1 1.80M and 954K.

## v3

Goal: in a repeated incident, make the warm fast mode and the deterministic no-op reflection actually trigger. This follows the v2 R2 findings above. No SREGym runs were started for this entry.

- **Merged `vic/fix/reflection-validator-rules` (8c90b1a).** The reflection prompt now states the MemoryValidator rules: the playbook index link and `<UPPER_CASE>` placeholders. This targets the 3-attempt R1 reflection in v2. Clean merge.
- **Merged `vic/fix/detector-watch-eval` (2f671e1), at the coordinator's request.** This is a controller-runtime fix from the agent that investigated the Go runtime:
  - informer events are coalesced per kind, so ConfigMap and Deployment events no longer queue behind a Pod-event backlog;
  - a detector below its Firing threshold is re-evaluated within 1 s;
  - late findings attach to an incident until its responder launches;
  - controller pod logs are exported to `sdo_runtime/controller_logs/`.

  It likely explains the v2 "unexplained detail": no incident-detector evaluation after the ConfigMap delete. The merge was clean. I made no Go changes of my own.
- **The v2 evidence was narrower than the brief assumed.** The brief said R2's `memory_reuse` showed "1 applied playbook". That playbook was `.sdo/playbooks/health-objective/README.md`: the request's `surfaced_playbooks` held only the health playbook, which is the health finding's playbook. The prior outcome's `applied_playbooks` was also the health playbook. The R2 responder ran the incident playbook's scripts but reported only the health playbook as applied. So "a surfaced playbook owned by an incident detector", read literally as `surfaced_playbooks`, would still have declined on v2.
  - **Decision:** a playbook counts as surfaced through any of these sources, recorded in `WarmPlaybookMatch.sources`:
    1. `active-finding`: an active finding of its owning detector;
    2. `surfaced`: it is in the request's `surfaced_playbooks`;
    3. `prior-outcome-applied`: the exact-fingerprint prior outcome applied it;
    4. `detector-learned-from-exact-prior`: its owning detector's `originatingIncident` is that exact-fingerprint prior incident.

    Source 4 is what fires on the v2 shape. It is still tight, because the detector was learned from the very incident that matched by exact fingerprint. Ownership is always required: the playbook must be listed in `possiblePlaybooks` of a registered `class: incident`, `owner: responder` detector in the worktree manifest. Health-owned, unregistered, and non-exact cases stay cold.
  - The warm prompt now asks the responder to list the applied playbook path exactly as shown. Later outcomes then record the incident playbook, so source 3 also holds from the next round on.
  - Rejected alternative: surface the incident detector's playbooks from Go (`surfacedPlaybooks` in `controller.go`). That would change the controller while another agent owns it, and the Python-side rule is enough.
  - Not added: declining warm mode when the incident detector evaluated `clear` after the fault became visible. With `Firing > 1` a clear status can mean "pending", so this could wrongly decline. The one combined sanity check covers contradiction instead.
- **Warm prompt for a detector that has not fired.** Each playbook gets an evidence line:
  - When the detector fired, the text is unchanged: the evidence establishes the preconditions, and the check replaces the playbook's diagnosis steps.
  - When it has not fired, the prompt says no incident-detector evidence exists yet. It asks for ONE combined sanity check over the detector's watched kinds in the namespace (plus the pods and events of the affected workload) against the preconditions the playbook states. If the playbook has a script whose name contains `diagnose`, `check` or `verify` (in that order), the check runs it; a verify script is expected to fail on the fault before repair.
  - If the check contradicts, the responder falls back to full investigation.
  - The cold prompt is unchanged.
- **No-op reflection.** All of these must hold:
  - the warm rule held;
  - the responder's `applied_playbooks` includes the warm playbook;
  - `status=completed` with confirmed root causes, and every repair action and verification passed;
  - the controller verified health (every final health detector state clear);
  - for each owning detector, its latest `incident_detector_states` entry is clear with no fingerprints, or it has no entry at all ("not evaluated after the response"). A detector that is firing after the response forces a full reflection.

  `reflection_skipped_reason` names:
  - the prior incident(s);
  - each playbook with its detector;
  - whether the detector `fired` or `had not fired` at dispatch;
  - the surfacing sources;
  - each detector's post-response state.

  Tradeoff: when the detector never fired, the no-op skips the only LLM turn that could have made a stale detector fire promptly, such as the v2 `Firing: 2` detector. I accepted this as specified. The controller-runtime merge and the new validator rule address promptness directly.
- **Reflection guidance and enforcement.** The reflection prompt now asks incident detectors to:
  - use `persistence.firing: 1` in the manifest and `Firing: 1` in `Spec()`;
  - watch where the fault is visible, including Pods and Events for pod-level symptoms (FailedMount, CrashLoopBackOff, ImagePullBackOff, OOMKilled, failing probes).

  The detector skeleton now shows Pod and Event watches with `Firing: 1`; it used to show `Firing: 2`, which probably seeded v2's choice. Playbooks are asked for `scripts/verify.sh` (and `diagnose.sh` when needed) because the warm path runs it as the sanity check.
  - **Enforcement decision:** `MemoryValidator._validate_incident_persistence` rejects a new or changed responder incident-detector registration with `persistence.firing > 1` (`INCIDENT_DETECTOR_MAX_FIRING = 1` in `operational_memory/models.py`). I put it in the validator, not the `DetectorRegistration` model, so existing repositories with legacy `firing: 2` detectors, such as the v2 workspace, still load. Untouched legacy registrations are grandfathered, so an unrelated playbook-only proposal is not rejected. Refining a legacy detector forces the fix. The Go `Spec()` must match the manifest (enforced by the builder), so checking the manifest is enough.
- **Validation.**
  - `format_code.sh` and `check_errors.sh` are clean.
  - `uv run pytest -q tests/unit/sdo tests/unit/libs/agent_cli tests/unit/benchmarks/sregym/adapter tests/unit/controller`: 354 passed, 2 skipped (after both merges).
  - `GOMAXPROCS=8 go test ./...` passes in `controller/runtime`, `controller/core` and `controller/sdk`.
- **Images rebuilt** at f68a717 with `BUILDX_BUILDER=sdo-example bash scripts/build_sdo_images.sh` (exit 0, smoke imports passed): `sdo-detector-validator:v0.1.0` 57b8a3289b53, `sdo-controller:v0.1.0` 05b293361ecb, `sdo-responder:v0.1.0` a4ca9282709f, `sdo-sregym-responder:v0.1.0` 03c6691fb16d.

### v3 SDO pipeline run (`20260927_133809_pipeline_sdo-codex-luna-reuse`)

- **Setup.** Code head `495c1db`; images built at `f68a717`, which is code-identical to head (docs-only diff). Image IDs were verified before launch: controller `05b293361ecb`, sregym-responder `03c6691fb16d`, responder `a4ca9282709f`, validator `57b8a3289b53`.
  - Same TOML, wrapper `run.sh`, cluster `luna-w0`, judge `codex-gpt-6-astra`/xhigh, and seed workspace (`<scratch>/seed/lifecycle_workspace`) as v2.
- **R1 (cold): pass/pass.**
  - Primary 91.4s. Diagnosis POST at +21.3s, TTL 42.4s, TTM 91.7s.
  - Gate: 121.8s plus a 6.5s injection request. Load 6.9 at injection, 6.3 at mitigation.
  - Lifecycle: reused with 0 tokens; revalidation took 213s.
  - Responder: 684,276 input (633,088 cached), 6,916 output, 24 requests, 159s.
  - Reflection: first attempt accepted with no retries. 1,594,697 input (1,466,112 cached), 18,869 output, 15 requests, 242s. Post-recovery 398s; stage wall 1,098s.
  - Learned detector (`missing-geo-init-configmap`): `Firing: 1`, watches Deployment, ConfigMap, Pod and Event.
  - Learned playbook: indexed, uses `<UPPER_CASE>` placeholders, and ships `scripts/repair.sh` and `scripts/verify.sh`.
- **R2, first attempt (kept as `stage_1_reused-incident.20260927_141722`): pass/pass, but primary 345.9s.**
  - Warm prompt confirmed from the rollout ("Warm path: validated incident memory matches...").
  - The incident detector fired at 14:06:03.6, 0.7s after the health detector, and was part of the dispatch (controller log iteration 5).
  - Reflection was the deterministic no-op: `reflection_skipped_reason` set, 0 attempts, 0 tokens. Responder: 448,173 input (413,440 cached), 3,099 output, 22 requests.
  - The responder finished the repair and verification and ran the mitigation submit at 14:06:46 (+37s). At that moment the conductor was still grading the diagnosis (xhigh judge, 14:06:26–14:06:46).
  - `Conductor.submit` answers a submit made during an evaluation with 200 "Submission already accepted" and **drops it**. `adapter/submission.py` then polled 300s for a terminal stage, exited 1 with "SREGym did not reach ... after submission", and the responder resubmitted at 14:11:54.
  - This is an adapter bug, introduced by making diagnosis non-blocking (`9d0e1d9`). The old comment said the conductor "queues a later mitigation submit", which is false. v1 and v2 never hit it because their repair took longer than the judge.
- **Fix `8e9b717`: mitigation waits for the conductor's `mitigation` stage before posting.**
  - Diagnosis still returns on acknowledgement, so repair keeps overlapping diagnosis grading.
  - Test-first: `test_mitigation_waits_for_diagnosis_grading_before_submitting`. The existing mitigation test was updated for the extra `/status` poll. Adapter tests pass (79); ruff and tach are clean.
  - Alternative rejected: patching SREGym's conductor to queue submits. That would change the harness the Codex baselines ran under.
  - Effect on the metric: the SDO mitigation timestamp can now include up to one diagnosis-judge duration (about 20s at xhigh) of waiting. That is a benchmark-imposed wait, the same one Codex would face.
- **Rebuild and relaunch.** `BUILDX_BUILDER=sdo-example bash scripts/build_sdo_images.sh` at `8e9b717`: sregym-responder `97de0407e563` (verified to contain the fix) and validator `26dad4eadc97`. Controller and responder IDs were unchanged.
  - R1 was kept: it was unaffected, because its mitigation POST came after diagnosis grading. Only R2 was rerun, via `run_sregym.sh <pipeline> --stage 1`, which re-chains R1's workspace.
  - The new validator identity triggered a pre-fault lifecycle revalidation (199s, 0 tokens), outside the primary metric.
- **R2 rerun (the reported R2): pass/pass.**
  - Primary 43.6s. Diagnosis POST at +21.0s, TTL 43.5s, TTM 43.8s.
  - Gate: 108.3s plus 6.4s. Load 6.8 at injection, 7.0 at mitigation.
  - Warm prompt confirmed from the rollout. The incident detector fired at 14:24:44.96 (controller log iteration 7) and was part of the dispatch.
  - Responder: 246,943 input (216,320 cached), 2,473 output, 12 requests, 77s. It was mitigation-ready at 14:25:26 (+36s); the adapter held the POST until the diagnosis verdict at 14:25:33.
  - Reflection: deterministic no-op (`reflection_skipped_reason` set), 0 tokens, `validator_skipped_reason=unchanged-diagnostics`, no controller update. Post-recovery 8.4s. Stage wall 567s.
  - Targets met: total R2 incident tokens 247K, below the Codex median (861K) and A2 (438K). Primary 43.6s, below v2's 51.7s.
  - Minor detour: after a successful mitigation the responder ran `submission --help` and listed tools before returning. About 20s, after the metric.

## Persistent controller

Goal: keep ONE SDO controller running across the rounds of a pipeline (the paper's long-running design) instead of installing a fresh controller per incident that exits after one closure. Branch `vic/feat/persistent-controller`; config `sdo_codex_luna_persistent.toml`.

### What the conductor does between problems (the blocker)

- **Every problem deletes and recreates the application namespace.** `Conductor.start_problem` calls `undeploy_app()` (hotel: `kubectl delete namespace hotel-reservation`, wait, then delete PVs) and then `deploy_app()` (create the namespace, apply the source-built manifests, wait for ready). After the agent signals cleanup, `_cleanup_sync` recovers the fault, runs `problem.app.cleanup()` (the same namespace delete), and then `reconcile_to_baseline()`.
- **Reconciliation deletes every namespace, PersistentVolume, ClusterRole, and ClusterRoleBinding that is not in the baseline.** Only `kube-system`, `kube-public`, `kube-node-lease`, `default`, `sregym`, and `chaos-mesh` are protected.
- **`preserve_infrastructure` does not change either behaviour.** It only reuses the shared infrastructure (metrics-server, OpenEBS, Prometheus, Jaeger, OTel, MCP) and makes it part of the persisted baseline. The application namespace is still recreated, and anything created after the baseline is still reconciled away.
- **Each stage is a separate `main.py` process, and each problem launches a separate driver process.** After `done`, the harness gives the agent 60s and then kills it. So the controller has to live entirely in the cluster, and teardown has to come from the pipeline runner.
- **A latent harness bug.** Hotel's `cleanup()` selected PVs with `kubectl get pv | grep 'hotel-reservation'`, which also matches the claim column of any namespace containing that substring (for example `hotel-reservation-sdo`), and then stripped their finalizers.

### Design chosen

- **One controller per application, in its own namespace, named after the application namespace: `<app-namespace>-sdo`** (`hotel-reservation-sdo`).
  - That namespace holds the controller Job, repository PVC, `sdo-controller-state`, the Lease, the credentials Secret, the maintenance ConfigMap, the SREGym submission bridge, and the responder and validator Jobs.
  - Responder Jobs run there because the RWO repository PVC is there. Their kubectl is pointed at the incident's application namespace through a generated kubeconfig.
  - The Go controller gained `--control-namespace`. `--namespace` stays the observed application namespace.
- **SDO stays off the application's manifests.** The application namespace receives only two Roles and RoleBindings: a read-only observer Role for the controller, and the existing `sdo-responder` repair rules for the responder. Both are bound to ServiceAccounts in the controller namespace. The responder's Role in its own namespace is reduced to publishing its result ConfigMap.
  - No ClusterRole or ClusterRoleBinding is created. The existence check for the application namespace is done by the host-side adapter with the trusted kubeconfig, so no cluster-scoped grant is needed.
- **Scope is per application (coordinator constraint).** Memory, PVC, state, Lease, and credentials belong to one application's controller. The pipeline state file is keyed by application namespace.
  - A stage for a different application installs a separate controller in its own namespace.
  - A stage that names a different application for an already-served namespace is refused.
  - Tests: `test_stages_for_different_applications_install_separate_controllers` and `test_refuses_to_reuse_a_controller_for_a_different_application_in_the_same_namespace`.
- **The harness keeps the namespace.** Opt-in `SREGYM_PRESERVE_NAMESPACE_LABEL` (in `third_party/sregym`, commit `ed505f3d` on the submodule branch `vic/feat/persistent-controller`, not pushed) keeps namespaces labelled `<label>=true`, and the PVs bound to their claims, out of reconciliation.
  - The runner sets it to `sdo.dev/controller-namespace` only when `persistent_controller` is on. The installer puts that label on every separate controller namespace.
  - Unset (every baseline run), reconciliation is unchanged.
  - The hotel PV selection now matches the claim namespace exactly. No baseline run ever had a namespace containing `hotel-reservation` other than the application's own, so this fix does not change any earlier result.
  - Alternatives rejected:
    - Rewriting the persisted baseline file from the adapter: it is harness-internal state and too fragile.
    - Putting the controller in the protected `sregym` or `default` namespace: that mixes SDO state with the harness's and breaks per-application scoping.
- **Maintenance pauses instead of stopping the controller.** Between problems the application is intentionally absent. Without a pause, the controller would treat that as an incident and dispatch a responder to "repair" the deleted namespace.
  - The operator-owned ConfigMap `sdo-controller-maintenance` (`state: paused|active`, `generation`) is polled every second.
  - While paused, the controller stops its informers and skips evaluations. It still finishes closure, reflection, and acknowledgement.
  - Resuming re-creates the informer generation (a fresh list, which also recovers from the 403s seen while the Roles were gone), logs `{"controller_maintenance":"active","maintenance_generation":G}`, and evaluates every detector once (`Controller.EvaluateAll`).
  - This is also a production-meaningful feature (planned redeploys).
  - Alternative rejected: scaling the controller away between problems. That violates "one controller pod for the whole pipeline".
- **Learned detectors reach the running controller.** A long-running controller never loaded detectors accepted by reflection: the rollout ran only after the Go binary exited, which production never did. `check_cli controller --supervise` now runs the Go controller with `--restart-after-closure`. After each acknowledged closure the controller exits 0, the supervisor compiles and records the rollout, logs `controller_supervisor: relaunch`, and relaunches in the same pod.
  - Production installs without `--exit-after-closure` or `--duration` now pass `--supervise` too, which fixes the same gap for `sdo operate`.
- **Production default.** `sdo operate` keeps single-namespace installation as the default. That is today's behaviour, and production namespaces are not recreated by a harness. A new optional `--controller-namespace` selects the split layout.
  - Idempotency is opt-in (`ControllerInstallConfig.reuse_existing`). A healthy controller Job whose install-fingerprint annotation (a hash of the rendered Job) matches is kept, and only the namespace-scoped grants and transport are re-applied.
  - `sdo operate` leaves it off: re-running operate after a new deployment should reseed memory from the verified deployment.
- **The persistent mode flag is `persistent_controller = true` under `[defaults.agent_config.sdo_codex]`. It is off by default** (tests: `test_persistent_mode_is_off_by_default*`).

### Stage sequencing in persistent mode

1. **Drain the previous incident, if one is pending.** Wait until it is acknowledged and the supervisor has relaunched the controller (so the reflection commit and any learned-detector rollout are live). Sync the controller's repository into this stage's workspace (the controller's repository is the source of truth), and write the previous stage's strict receipt with `reflection_drain.{drained_by, waited_seconds}`. The wait is recorded as this stage's `reflection_drain_seconds`, a pre-injection cost.
2. **Reuse the controller** if it is the one this pipeline installed (same pod UID in the state file) and the deployed lifecycle context has the same fingerprint (a hash of the health objective and active resources). Reuse skips lifecycle revalidation, re-applies the application-namespace grants, and re-applies the bridge.
   - Otherwise: delete any controller this pipeline did not install, run lifecycle as before, and install fresh.
   - If the topology changed, memory is drained into the workspace before the reinstall, so learning is not lost.
3. **Resume with a new generation.** Gate injection on the first all-clear evaluation after `controller_maintenance: active` for that generation, never on the Job creation time.
4. **Wait for this stage's own incident.** That is the first incident ID not known before injection whose `pending_closure.verified_at` is set. Then pause and wait for the controller to acknowledge the pause.
5. **Write `sdo_incident_resolution.json`.** Submit the recorded result if the responder did not, then signal cleanup. The stage does not wait for reflection.
6. **Pipeline teardown** (runner, always, including after a failure) runs `python -m benchmarks.sregym.adapter.persistent teardown --state <pipeline>/sdo_persistent_controller.json`. It drains the last incident, writes its strict receipt, and deletes the controller namespace. The runner validates the deferred strict receipts after teardown and fails the pipeline if any is missing or invalid.

### Asynchronous reflection (coordinator clarification)

- **Resolution metrics end at the incident's own milestones.** The primary metric is conductor-side (mitigation POST minus injection). `incident_resolution_seconds` is controller detection to controller-verified health, taken from the closure's timestamps. Neither can include reflection.
  - Tests: `test_stage_reports_resolution_at_verified_health_before_reflection_finishes` asserts 40s from detection to verification while the fake reflection has not even started. `test_next_stage_injects_only_after_previous_reflection_is_committed_and_rolled_out` asserts the drain is recorded as a pre-injection cost, and that the deferred receipt keeps 40s while its 240s of post-recovery learning is listed as excluded.
- **The stage reports resolution as soon as verification happens.** The strict receipt, which needs the reflection commit, is written by the next stage's drain or by teardown.
- **The next injection waits for the drain.** It waits for reflection to be durably committed and for the supervisor to relaunch (rolling out any learned detector) before resuming and injecting. The test asserts `reflected < receipt < fault` ordering.

### Other decisions

- **Responder Jobs of earlier incidents coexist** in the controller namespace until their TTL. In persistent mode the receipt's live-Job consistency check ignores Jobs of other incidents; the durable request/result pair is still required to be unique.
- **Merged `vic/perf/controller-api-rate`** (`0fcd065`, `1643124`: QPS 50 / burst 100, one cached-resourceVersion state update, and the `ErrEffectNotDurable` guard) into this branch before the live check, as the coordinator asked. The merge was clean: it touched `controller.go`, `effects.go`, `broker_effects.go`, and `state_store.go`, while this branch touched `controller.go` only in a new method. Go tests pass for `runtime`, `core`, and `sdk`.

### Bugs found by the live check (fixed test-first)

- **Deferred receipts landed in an orphaned staging directory** (run 1, `20260927_163003_pipeline_sdo-codex-luna-persistent`). SREGym publishes `.runtime/<agent>/<opaque id>` to `results/<agent>/<problem>/run_N` when a problem ends, so the receipt that the later drain wrote never reached the results tree.
  - Fix (`201fea0`): the pipeline state records `deferred_receipts`, and teardown `--publish-root` copies each drained receipt and drain-time controller log into the published run. The run is matched by the `incident_id` in its `sdo_incident_resolution.json`, and the opaque ID is canonicalized to the problem ID.
  - A receipt that fails validation is kept as `sdo_rejected_production_receipt.json`.
  - Each deferred stage is judged by its own receipt.
- **A repeated mitigation call blocked for 300 s and turned a solved incident into `status: failed`** (runs 1 and 2).
  - The responder re-ran `adapter.submission mitigation` after the problem had ended (`awaiting_cleanup`). The command waited for a `mitigation` stage that never reopens, the responder reported `status: failed`, and the drained receipt failed `completed=true`. Both oracles had passed.
  - This is not persistent-specific: the same transport serves per-problem runs.
  - Fix (`69ef2fe`): a mitigation call on a finished problem returns `already_submitted` at once, and the responder instructions say to submit mitigation once. Images were rebuilt afterwards (sregym-responder `7a5216c1817c`).

### Live result (kind `luna-w0`, lifecycle seed 64b3ac2, judge codex-gpt-6-luna xhigh)

Pipeline: `third_party/sregym/logs/20260927_174023_pipeline_sdo-codex-luna-persistent` (rc=0, 20.5 min wall).

| | stage 0 (install) | stage 1 (reuse) | v3 stage 0 | v3 stage 1 |
|---|---|---|---|---|
| Diagnosis / Mitigation oracle | pass / pass | pass / pass | pass / pass | pass / pass |
| Controller pod UID | `60db893c…` | `60db893c…` (same) | per-round pod | per-round pod |
| Primary (mitigation POST − injection) | 96.0 s | 57.2 s | 91.4 s | 43.6 s |
| `incident_resolution_seconds` | 155.6 s | 89.6 s | 196.0 s | 112.2 s |
| Inventory + lifecycle revalidation | 210.1 s | 4.1 s (skipped) | 213.3 s | 198.6 s |
| Controller install | 13.7 s | 2.0 s (reused) | per round | per round |
| Controller baseline gate | 107.3 s | 1.2 s | 121.8 s | 108.3 s |
| Reflection drain before injection | 0 | 158.1 s | (inside previous round) | (inside previous round) |
| Injection deferred (conductor view) | 338.8 s | 173.5 s | 342.5 s | 314.4 s |
| Responder tokens in / out | 340k / 3.8k | 246k / 2.9k | 684k / 6.9k | 247k / 2.5k |
| Reflection tokens in / out | 841k / 13.0k | none (playbook reuse) | 1.59M / 18.9k | none |

- **Stage 1 skipped install, revalidation, and baseline**: `installed_this_stage=false` and `lifecycle_revalidation_skipped=true`. Per-round setup fell from about 307 s + install (v3) to 7.3 s, saving about 300 s.
  - The previous incident's reflection (158 s in this run) now finishes before injection, as a measured `reflection_drain_seconds` that is not counted in resolution. So the net pre-injection time for stage 1 was 173.5 s versus v3's 314.4 s (−141 s).
- **The receipts are per incident**:
  - Each strict receipt's `incident_id` matches its stage's resolution record.
  - There is one responder Job per incident.
  - `controller_namespace=hotel-reservation-sdo`.
  - Stage 0 was drained by stage 1 (`reflection_commit` b2389015, learned-detector rollout recorded).
  - Stage 1 was drained by pipeline teardown.
  - After teardown the cluster has no `hotel-reservation-sdo` namespace, and only the `observe` PVs remain.
- **Stage 0's primary time and resolution are within run-to-run variance of v3.** One sample per stage; the model responder dominates both.

## Program integration: persistent controller + variants, sequence, fresh reflection

Branch `vic/exp/program-integration`, merge of `vic/feat/persistent-controller` (28c99f3, which already contains `vic/perf/controller-api-rate`). No SREGym runs and no image rebuilds were started for this entry.

### Conflict resolution

- **Both CLI flags survive.** The driver keeps `--reflection-session {resume,fresh}` and `--persistent-controller`.
  - **Bug found in the merge:** the persistent path builds its own `RuntimeConfig` and did not pass `reflection_session`. A persistent run configured with `reflection_session = "fresh"` would have silently resumed. It now passes the mode; `test_persistent_driver_reports_resolution_without_strict_receipt_or_job_cleanup` is parametrized over both modes and failed before the fix.
- **`ControllerInstallConfig`** keeps `reflection_session` next to `controller_namespace` and `reuse_existing`. The broker args carry `--reflection-session` and `--responder-turn-log`, so the install fingerprint (a hash of the rendered Job) covers the reflection mode: a persistent controller installed with a different mode is replaced, not reused. The fingerprint-mismatch test covers this case.
- **Receipts.** `_production_receipt` keeps the incident-scoped signature from persistent mode (`incident_id`, other incidents' Jobs tolerated) and the `_same_session_reflection` helper from fresh reflection. Deferred persistent receipts therefore report `reflection_session_mode` and `same_session_reflection` the same way per-problem receipts do.
- **Submodule.** `third_party/sregym` points at `38cbf4c7`, a merge of `ed505f3d` (namespace preservation, `SREGYM_PRESERVE_NAMESPACE_LABEL`) into `f0160350` (missing-ConfigMap variants), on submodule branch `vic/exp/program-integration`. It merged cleanly (different files). It is not pushed.

### Config choices

- **Persistent mode is the production shape** for the multi-stage SDO experiments. The paper's controller is long-running, and the sequence/variants questions (cost over a sequence, warm path on similar incidents) are about one controller accumulating memory. So `sdo_codex_luna_variants.toml` and `sdo_codex_luna_sequence.toml` now set `persistent_controller = true` in place, and their defaults equal `sdo_codex_luna_persistent.toml`'s. Neither had been run yet, so no earlier result changes meaning.
  - Rejected: separate `_persistent` copies next to per-problem variants/sequence configs. That doubles the configs with no planned per-problem arm; the per-problem shape stays measurable through `sdo_codex_luna_reuse.toml`.
  - All four sequence faults and all three variants are Hotel Reservation problems in `hotel-reservation`, so one controller (`hotel-reservation-sdo`) serves every stage. The pre-injection lifecycle fingerprint is the deployed topology, which faults do not change, so the controller is reused across different faults.
- **Persistent reuse configs.** `sdo_codex_luna_persistent.toml` (from the persistent branch) is the persistent counterpart of `sdo_codex_luna_reuse.toml` and differs only in `persistent_controller` and the pipeline name; a test pins that. I added `sdo_codex_luna_persistent_fresh.toml`, the persistent counterpart of `sdo_codex_luna_reuse_fresh.toml` (adds only `reflection_session = "fresh"`). I kept the existing file name instead of renaming it to `..._reuse_persistent`, because the live run on `vic/feat/persistent-controller` uses it.
- **Codex baselines** (`codex_luna_*`) have no controller and are unchanged.
- Every config keeps `judge_model_id = "codex-gpt-6-luna"`; the config tests assert it for every resolved stage.

### Analysis fix for persistent mode

- The controller's per-turn usage log (`sdo_runtime/usage/controller-turns.jsonl`) lives on the controller PVC. With one controller per pipeline it accumulates every incident, and each stage exports the whole file. `incident_cost.py` summed it as the stage's reflection turn time, which would double-count in persistent mode.
  - It now counts only records whose `cwd` is the incident worktree of the receipt's `incident_id`. The broker runs reflection in that worktree, and the directory name comes from the new `sdo.operational_memory.worktrees.incident_worktree_dirname` (extracted from `WorktreeManager.path_for`, unchanged behaviour).
  - Receipts without an `incident_id` keep the old unfiltered sum. Token columns already come from the per-incident receipt and were unaffected.

### Test-suite fix

- The three `TestGrepProperties` Hypothesis tests (legacy crucible grep tool) now use `deadline=None`. Each example creates a temp directory, writes a file, patches the cwd, and compiles an arbitrary regex, so per-example wall time follows filesystem and machine load. Under load that exceeded the default 200 ms deadline. Example counts are unchanged; the file runs in about 5 s.

### Merged 201fea0 (publish drained receipts) and composed it with per-incident usage scoping

- `201fea0` merged cleanly. Its teardown publishes the drained strict receipt and controller log from the orphaned staging directory into the run directory SREGym already published.
- **Gap found:** the drain also exports the controller PVC's runtime evidence (`sdo_runtime/usage/*.jsonl`, Codex/Claude transcripts) into that staging directory, and it was not published. The persistent stage itself exports only controller logs, so published runs had no `controller-turns.jsonl` or rollouts. `incident_cost.py` would have reported no reflection turn time and no warm-prompt evidence for persistent stages.
- **Fix:** `publish_deferred_receipts` also moves every file under the staging `sdo_runtime/` into the run directory, byte for byte. Only the receipts and controller logs get the opaque-ID rewrite. The published usage log is cumulative, and the per-incident `cwd` filter from `15efd16` then scopes it to the stage's incident. Responder rollouts were already scoped by `responder_session_id`. `test_deferred_receipts_are_published_into_the_run_the_harness_already_published` asserts the usage log and a transcript are published and the staging directory is removed.

## Timed replication, variants, and sequence (program integration on main)

Owner: autonomous agent. Every decision below lists what was chosen, the alternatives, and why.

### Step 0: keep learned playbooks within the responder's RBAC (`24a01b8`)

- **Root cause.** The stage-0 reflection's `verify.sh` ran `kubectl -n "$NAMESPACE" exec deployment/<frontend> -- wget ...`. The responder Role has no `pods/exec`. The reflection prompt itself asked for "a `kubectl exec` or `curl` against the entrypoint", so the model followed the prompt.
- **Decision: do not widen RBAC.** The prompt now lists what the responder may run (get/describe/logs/watch, ConfigMap create/apply/patch, workload patch and `rollout restart`, delete pod, delete NetworkPolicy) and what it cannot (`kubectl exec`, `port-forward`, `attach`, `cp`). The representative-request example became a `python3` urllib call to `http://<SERVICE>.<NAMESPACE>.svc:<PORT>/`, because the responder image has python3 but no curl or wget.
  - Alternative rejected: suggesting curl. It is not in the image, and adding it changes images for no gain.
- **Decision: the validator checks only changed playbook files.** `MemoryValidator` runs on every proposal, including outcome-only appends. A check over all playbooks would make every later outcome append fail in a repository that already holds an exec playbook. That is the same "untouched legacy" rule the incident-persistence check uses.
  - Alternative rejected: checking every playbook. It is stricter, but it can brick an existing memory.
- **Decision: match a kubectl invocation whose subcommand, after at most four flag or value tokens on the same command, is one of the four verbs.** A verb directly followed by a backtick is prose (`` `kubectl exec` ``), so a playbook may still say what not to do. `kubectl debug` (ephemeral containers, also not granted) is not included: the task named four verbs, and no run has produced it.
  - Alternative rejected: a plain substring match. It rejects prose and names such as `deployment/exec-proxy`.
- **The verb list is one constant** (`RESPONDER_FORBIDDEN_KUBECTL_VERBS`) that feeds both the prompt and the validator. A contract test asserts that the responder Role in `controller/runtime/deploy/rbac.yaml` grants none of `pods/exec`, `pods/portforward` or `pods/attach`. `sdo-memory-check` prints the fix.
