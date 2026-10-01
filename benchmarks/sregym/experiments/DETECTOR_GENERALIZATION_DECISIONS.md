# Detector generalization: decisions log

Branch `vic/exp/detector-generalization` (worktree `/mnt/data/shli/sdo-worktrees/detgen`), from `vic/exp/stream-learning-curve`.

## Phase 0/1 (done)

- **Flag**: `--reflection-guidance {baseline,generalize}`, plumbed through `ControllerInstallConfig.reflection_guidance`, the broker CLI, the SREGym driver (`agent_config.sdo_codex.reflection_guidance`) and fastloop. Alternatives: a separate image or env var (rejected: arms must share images and the install fingerprint must change with the flag). Documented in `docs/feature-flags.md`.
- **Brief** (generalize only): each incident detector gets its Spec description, whether it sets ParameterBindings, and a 1400-char `Detect` excerpt. Memory section moved before the shell-command section and its budget raised to 9000 chars so the global clip drops commands first.
- **Guidance** (generalize only): a five-step generalization protocol (compare with every detector, widen same-class detectors and report via ParameterBindings, parameterize playbook, new detector only for a different cause, keep a near-miss test), replacing the "sharp fault-specific" and "without generalizing" directives. First-time detectors are also asked to key on the class-level condition.
- **Leak scrub (applies to both arms, deviation from "control = current prompt")**: the existing shared wording named fault-tied terms (pod failure reasons, ConfigMap/Secret mount, NetworkPolicies, Service DNS). These were genericized in both arms so the leak test can pass. The trusted SDK API reference (`DETECTOR_SDK_REFERENCE`, shared with the lifecycle) and runtime data (outcome JSON, repository-derived brief content) are exempt from the scan. The control therefore differs slightly from the earlier stream's prompt.
- **Not done**: the broker's "successful reflection must include a detector update unless a learned detector fired in the request findings" check was left unchanged; it may reject playbook-only parameterization when a widened detector fires after dispatch (seen twice in the earlier stream).
- Tests first: `test_reflection_generalization.py` (leak denylist scan of prompt and static brief, guidance text, brief detail, bounding, CLI/driver/install plumbing).

## Instruction change and decisions made without the user (2026-09-30)

- **Quota**: the user relayed "run without checking quota". I never read `~/.codex` (the read had been denied) and replaced the 97% rule with: stop launching LLM turns on a usage-limit or rate-limit error. No such error has occurred so far. Quota at every phase boundary: not checked (last known 91%).
- **Leak test made robust** (user-directed): `_leaks(..., runtime_paths=...)` strips the pytest temp directory and ISO timestamps before scanning, so digits in `/tmp/pytest-of-shli/pytest-1000/...` no longer false-positive. Regression: `test_leak_scanner_ignores_temporary_paths_that_contain_digits`; the suite passes with `--basetemp` under `pytest-1000`.
- **Control prompt not byte-identical** to the earlier stream (shared wording was genericized for the leak test). Accepted by the user.
- **Broker rule** (playbook-only reflection rejected unless a learned detector fired in the request findings): checked in the small stream, not a blocker. In the small stream the incident detector fires several controller iterations after the health detector dispatched the incident, so the request findings hold only `health-objective`; A2 and A3 reflections still changed the detector test or source, so the rule did not reject them. No code change. Re-check in the full stream.

## Phase 2: reflection replay (feasible, one iteration)

- **Tool**: `benchmarks/sregym/analysis/reflection_replay.py`. The broker ledgers under `<stage>/application_workspace/.git/sdo-broker/*.json` keep each incident's full `BrokerClosure`, `outcome_commit` and session mode. The tool clones the workspace at `outcome_commit` and runs the production `SessionReflector` (fresh mode, brief from the real `incident_brief`) with `CodexSessionBackend` semantics, except access `workspace-write` so the scratch clone is the only writable tree (alternative: `danger-full-access` as in the pod; rejected on a shared machine). Tests: `test_reflection_replay.py`.
- **Tuning case**: stream stage 10 ledger `...93107139`, the readiness variant on `reservation` after `frontend_readiness_probe_mismatch` (created for the original frontend incident) and `geo_image_config_mismatch` already existed. The original reflection (resume, baseline) added the sibling `reservation_readiness_probe_mismatch` (1.09M input tokens).
- **Iteration 1** (wording as committed in 95f3528, no change needed): baseline guidance replayed added the sibling `reservation_grpc_readiness_mismatch` plus a new playbook (976k input tokens, 15 requests); generalize guidance widened `frontend_readiness_probe_mismatch` (no new detector id, playbook modified, `ParameterBindings{"WORKLOAD": ...}`, new matching case and two near-miss cases kept/added; 1.72M input tokens, 25 requests). Outputs in `/mnt/data/shli/detgen-runs/replay1/`. No wording change, so iterations 2-3 were not used (0 of the remaining budget spent). Held out and never inspected while tuning: family B, and the later instances of family A (recommendation, search, profile).
- Observation: the widened detector keys on "readiness probe port not declared by the container" plus an Unhealthy event; it is broader than the original frontend-only predicate but still requires the class-level condition.

## Phase 3: small live stream (treatment arm)

- **Variants are distinct workloads** (registry, `third_party/sregym/sregym/conductor/problems/registry.py`): A1 `frontend`, A2 `geo`, A3 `reservation`; B1 `mongodb-geo`, B2 `mongodb-rate`, B3 both.
- **Seed**: stage 0 starts from a copy of the earlier stream's stage-0 workspace (lifecycle-only: `.sdo` holds goal, arch, health detectors, one health playbook, an empty `outcomes.jsonl`), passed through `SREGYM_APP_WORKSPACE_SEED_DIR`. Alternative: a cold lifecycle per arm (about 15 minutes and 1M tokens each). Copy: `/mnt/data/shli/detgen-runs/seeds/lifecycle-stream`.
- **Images**: `sdo-{controller,sdo-sregym-responder,detector-validator}:detgen1` built from commit 95f3528 (`SDO_IMAGE_TAG=detgen1 BUILDX_BUILDER=sdo-example scripts/build_sdo_images.sh`); `v0.1.0` and `stream1` untouched. Cluster `detgen-w20` (1 control plane + 1 worker, 3 CPUs, prefix via `SREGYM_KIND_CLUSTER_PREFIX=detgen-w`, `SREGYM_WORKER_ID_OFFSET=20`), deleted afterwards.
- **Result** (pipeline `20260930_190803_pipeline_sdo-codex-luna-detgen-small`): 4/4 solved, strict-receipt pipeline completed. Evidence kept in `/mnt/data/shli/detgen-runs/small-evidence/`.

| Incident | TTD s | TTM s (judge-free) | responder in tok | reflection in/out tok | reflection outcome | incident detectors after |
|---|---|---|---|---|---|---|
| A1 frontend | 22 | 68 | 584k | 536k / 8.8k | created `readiness-probe-port-mismatch` + playbook (class-level, no workload name, `ParameterBindings{DEPLOYMENT}`) | 1 |
| A2 geo | 32 | 88 | 539k | 220k / 3.7k | no new detector; added a geo test case to the existing detector, parameterized playbook/scripts | 1 |
| B1 mongodb-geo | 26 | 69 | 523k | 433k / 4.8k | created `deployment-configmap-missing` + playbook | 2 |
| A3 reservation | 20 | 59 | 364k | 479k / 5.6k | widened `readiness-probe-port-mismatch` (source + tests; gRPC listener case), no new detector or playbook | 2 |

- **Firing evidence (controller log)**: the readiness detector fired at A2 with `ParameterBindings{DEPLOYMENT: geo}` (first at controller iteration 17) and at A3 with `{DEPLOYMENT: reservation}` (iteration 17); during B1 only `health-objective` fired (mongodb-geo), never the readiness detector. `incident_detector_states` in the closure is empty for every incident and the request findings hold only `health-objective`, because the health detector dispatches the incident at iteration 1 and the incident detector fires about 16 iterations later; so the firing is evidenced only by the controller log, not by the closure or `outcomes.jsonl`. It therefore did not shorten detection or dispatch; the benefit is memory shape (one detector, one playbook) and cheaper reflection, not earlier dispatch.
- **Why A2 needed no detector change**: A1's reflection wrote the detector at class level from the start (no workload name, reports the affected Deployment), so the geo incident matched as is; reflection only added a geo test case and parameterized the playbook scripts. A3 (reservation) did change the detector because the gRPC-served listener produced a different probe failure message.
- **Gate**: (i) met; (ii) met; (iii) met (4/4, pipeline completed with `require_strict_receipt`); (iv) partially: the warm flag is true at A2, A3 and also B1 (it is a fingerprint/rule match, weak as a discriminator), reflection at A2 cost 41% of A1's, but A3's widening cost 89% of A1's. Decision: pass the gate (the structural goal held and there was no cross-family firing); the weak part is reported.

## Phase 4 launch

- Treatment (full stream, 9 incidents, flag on, fresh reflection) on cluster `detgen-w22` (offset 22) and truncated control (A1 A2 B1 A3 B2, flag off, **resume** reflection as in the earlier stream, so control differs from treatment in two settings, as the task specified) on `detgen-w23` (offset 23). Same images `detgen1`, same seed, started 20 seconds apart.

## Phase 4 events and manual steps (disclosed)

- **Control extension** (orchestrator decision): after the 5-incident control finished at 2 incident detectors and 2 playbooks (same count as the treatment at that point), the control pipeline is extended to the full 9-incident order. `sdo_codex_luna_detgen_control_full.toml` (same settings, flag off, resume reflection); I overwrote the control pipeline's `pipeline_config.toml` snapshot through `write_pipeline_snapshot` (the old 5-stage snapshot is kept at `/mnt/data/shli/detgen-runs/control-snapshot-5stage.toml`) and resumed with `run_sregym.sh <pipeline dir>`; stages 0-4 are skipped as completed and stage 5 (A4) seeds from stage 4's workspace (17 commits, all 5 incidents). The controller was torn down at the end of the first control run and is reinstalled on resume.
- **Treatment A5 (search) could not deploy**: the A4 (recommendation) responder's source repair committed `kubernetes/reccomend/recommendation-deployment.yaml` with the live patched manifest, including a hard-coded cluster-specific image tag and a duplicated `imagePullPolicy` key (commit c31d5e7). At the next stage's redeploy `kubectl apply -k` failed ("mapping key imagePullPolicy already defined"), the application never came up, the fault could not be injected and the stage sat idle. Not a reflection problem and unrelated to the flag; it is a responder source-repair hazard of the persistent-workspace mode (the control can hit it too).
- **Handling (manual)**: I interrupted my own treatment pipeline (SIGINT to the runner, then to its orphaned stage child and worker child, both started by me, because the orphan still held the cluster lock), copied the authoritative repository from the treatment controller's PVC (`/workspace/application`, 7 incidents, `/mnt/data/shli/detgen-runs/treatment-pvc-after-b3`), restored three lost file modes, and committed one manual commit that deletes the duplicated `imagePullPolicy` line (`da664ab`, author `detgen-operator`). That repository is the `lifecycle_seed_stage7` of the treatment pipeline and stage 7 was resumed from it. The first attempt at A5 is recorded as "not manifested: application could not deploy (invalid manifest left by the A4 source repair), stage interrupted after about 40 minutes", never as a time. Operational memory (`.sdo/`) was not edited.

- **Re-seeding the resumed stage**: the first resume reused the existing `stage_7` directory (and its broken application workspace); I interrupted it, and reran with `run_sregym.sh <dir> --stage 7`, which renames the old directory to `stage_7_...20260930_215448` and seeds the new one from `lifecycle_seed_stage7` (manifest fixed, `da664ab`). Second manual fact: the A5 result is from that rerun; the lost first attempt cost about 40 minutes of wall clock and no measured time.

## Phase 4 results (both arms finished; clusters `detgen-w22` treatment and `detgen-w23` control deleted afterwards)

Images `detgen1` for both arms, commit 95f3528 plus later test/tool-only commits. Every incident in both arms was solved with the pipeline's strict-receipt check on (`require_strict_receipt`). Tools: `detgen_table.py`, `detgen_growth.py`; raw evidence in `/mnt/data/shli/detgen-runs/{treatment,control}-evidence` and the pipelines under `third_party/sregym/logs/*detgen-{treatment,control}`.

Incident-detector / playbook count after each incident (playbooks equal detectors in every cell):

| After | A1 | A2 | B1 | A3 | B2 | A4 | B3 | A5 | A6 |
|---|---|---|---|---|---|---|---|---|---|
| Treatment (generalize, fresh) | 1 | 1 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Control (baseline, resume) | 1 | 1 | 2 | 2 | 2 | 3 | 3 | 3 | 4 |

- Control grew at A4 (`recommendation_readiness_refused`) and A6 (`profile_readiness_mismatch`, a gRPC-port variant), i.e. a sibling readiness detector for two of the five variants; the other variants reused the class-level detector. Treatment never added a sibling; A2, A3, A4, A5, A6 were reflection "updated" (test case and playbook changes, A3 also a detector widening) and B2 was a playbook-only update.
- Firing (controller logs): in the treatment the readiness detector fired only on readiness incidents (geo, reservation, recommendation, search, profile), the configmap detector only on the mongodb incidents, each with `ParameterBindings{AFFECTED_DEPLOYMENT: <workload>}`; no cross-family firing. The control's detectors fired only on their own family as well (control log covers A4 to A6 only; its first controller's log was overwritten by the watcher, disclosed).
- Judge-free TTM (s), treatment vs control: A1 87/73, A2 119/96, B1 181/59, A3 50/57, B2 64/36, A4 121/28, B3 24/60, A5 44/43, A6 39/34; medians 64 vs 57. No TTM benefit shown; the treatment was run concurrently with the control at host load 10-33 and n=1.
- Tokens (input, k): responder sum 3.9M treatment vs 3.2M control; reflection sum 3.76M vs 4.60M; combined 7.7M vs 7.8M, no difference beyond noise.
- Near-duplicate score of final incident-detector source (names, numbers, strings normalized): control readiness detectors pairwise 0.52-0.66 similarity across three siblings (not near-identical after normalization, because the repeated predicate shapes differ), treatment has a single readiness detector.
- B3 (both configmap targets) triggered a deterministic no-op reflection in both arms (no reflection turn).

## Phase 5: ablation arm (baseline guidance + fresh reflection), 2026-09-30/10-01

Purpose: separate the two settings confounded in Phase 4 (treatment = generalize + fresh, control = baseline + resume). Arm: `reflection_guidance = "baseline"`, `reflection_session = "fresh"`; `sdo_codex_luna_detgen_ablation.toml` is `control_full` with only the reflection session changed. Same images `detgen1`, same seed, same 9-incident order, Codex gpt-6-luna for agent and judge (judge time not in TTD/TTM), new 1+1 cluster `detgen-w24` (offset 24, deleted at the end), host load 15-19 at launch. No raw/memoryless Codex baseline was rerun (nothing changed there). No usage or rate-limit error occurred. Pipeline `20260930_223227_pipeline_sdo-codex-luna-detgen-ablation`; evidence (controller logs per stage) in `/mnt/data/shli/detgen-runs/ablation-evidence/`.

**Disclosed manual step (B3 deploy failure, a responder source-repair hazard of the persistent workspace)**: stage 6 (B3) failed all 3 deploy attempts ("Not all pods ready within 600 s"; no result CSV) and the pipeline aborted at 23:45. Cause, reproduced on the idle cluster: the B2 responder's source repair committed `kubernetes/rate/mongo-rate-script.yaml` with doubled backslash line continuations (`\\` instead of `\`; the geo script is correct). `mongodb-rate` then runs `mongo ... \\` as `failed to load: \`, the admin user never gets `readWrite` on `rate-db`, and `rate` crash-loops (CrashLoopBackOff observed on the worker). Not a quota, infra or flag problem. Unexplained: A4 (stage 5) deployed from the same file and was solved; not investigated further. Recovery as in the Phase 4 A5 case: copied stage 5's `application_workspace` (clean tree, `c72a33c`, lifecycle provenance present) to `lifecycle_seed_stage6`, one manual commit by `detgen-operator` (`5be0d39`) restoring the single backslash on the two lines, then `run_sregym.sh <pipeline dir> --stage 6` (old stage 6 kept as `stage_6_...20261001_000253`; stages 0-5 not rerun). The failed first attempt is "not manifested: deploy failed", never a time (about 35 minutes wall clock). `.sdo/` memory was not edited. The controller was reinstalled for the resume (new controller log `dhxm2` from stage 6 on).

Results: all 9 incidents solved (strict-receipt pipeline completed).

Incident-detector / playbook counts after each incident (`detgen_growth.py`, health detector/playbook excluded; playbooks equal detectors in every cell):

| After | A1 | A2 | B1 | A3 | B2 | A4 | B3 | A5 | A6 |
|---|---|---|---|---|---|---|---|---|---|
| Treatment (generalize, fresh) | 1 | 1 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Ablation (baseline, fresh) | 1 | 2 | 3 | 4 | 5 | 6 | 6 | 7 | 8 |
| Control (baseline, resume) | 1 | 1 | 2 | 2 | 2 | 3 | 3 | 3 | 4 |

Ablation final memory: six per-workload readiness detectors (frontend, geo, reservation, recommendation, search, profile) plus `missing_workload_configmap` and `mongodb_rate_missing_init_configmap`; each readiness incident but B3 produced a new workload-named detector (fresh reflection, no gap in A2 to A6 except B3's no-op reflection and a reuse at A4 B2).

Per incident (ablation): TTD s / judge-free TTM s / responder in tok / reflection in tok:

| | A1 | A2 | B1 | A3 | B2 | A4 | B3 | A5 | A6 |
|---|---|---|---|---|---|---|---|---|---|
| TTD | 14 | 14 | 36 | 22 | 19 | 30 | 11 | 31 | 19 |
| TTM | 96 | 34 | 86 | 46 | 35 | 55 | 25 | 94 | 57 |
| resp in (k) | 447 | 302 | 661 | 533 | 361 | 356 | 187 | 469 | 427 |
| refl in (k) | 328 | 255 | 410 | 340 | 378 | 544 | 0 | 443 | 445 |

TTM medians: ablation 55 s, control 57 s, treatment 64 s. Tokens (input): responder 3.74M, reflection 3.14M, combined 6.89M (treatment 7.7M, control 7.8M). B3 again had a deterministic no-op reflection (0 tokens).

Firing (controller logs): the B-family detectors fired only on the mongodb incidents (`missing-workload-configmap` on `mongodb-rate` at B2 and on `mongodb-geo`/`mongodb-rate` at B3; `mongodb-rate-missing-init-configmap` on `mongodb-rate` at B3). No readiness detector fired at any incident (each is named for its workload, so none matched a different workload), and no B detector fired during A5/A6, so no cross-family firing and no cross-workload reuse inside family A. Caveat: the controller log is cumulative per controller instance and only records firings after the health detector's dispatch.

### 3-way comparison and takeaways

- **Meaning**: with fresh (handoff) reflection and baseline guidance the memory grew by one detector per new workload variant (8 detectors, 8 playbooks after 9 incidents), faster than the resume control (4) and far faster than the treatment (2). So fresh reflection alone does not produce the treatment's compactness (it was worse than resume); the generalize guidance and brief carry the effect. The resume session's shared context apparently made reflection partly reuse earlier detectors (it reused the class-level detector for 3 of 5 readiness variants); fresh reflection without the detector brief and protocol had no incentive to reuse.
- **Confidence**: moderate for "guidance, not session mode, drives compactness" (a 2/4/8 monotone ordering with the same seed, images and order); low for any statement about the size of the session effect (n=1 per arm, concurrent hosts at load 10-33, the ablation ran at 15-30, and the control differs in being resume).
- **No operational cost difference**: all arms solved 9/9; judge-free TTM medians 55/57/64 s are within noise; combined tokens 6.9M/7.8M/7.7M. The benefit shown is memory shape, not speed.
- **Implication**: adopt the generalize guidance (and its detector brief) for reflection; the fresh-session handoff by itself is not a substitute and, if used without the guidance, inflates memory. A detector that is tied to one workload name does not give earlier detection for later siblings.
- **Next step**: repeat treatment and ablation with a second seed or a different fault family (e.g. network or scheduling variants) to bound variance, and run the generalize guidance with resume reflection (the missing 2x2 cell) to complete the factorial.
- **Caveats**: n=1 per arm; the three arms were not run at equal host load; the control's first controller log was overwritten (earlier note); the ablation had one manual source-manifest recovery at B3 (disclosed above) and the B3 reflection is a no-op in all arms; near-duplicate score was not recomputed for the ablation.
