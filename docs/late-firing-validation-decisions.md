# Late-firing validation decisions

Branch `vic/exp/late-fire-integration` = `vic/exp/spec-first-detectors` (6058318) + `vic/feat/detector-firing-telemetry` (14dcace).

## D1 Merge
- What: `git merge --no-ff` of the telemetry branch into the spec-first branch.
- Alternatives: rebase, cherry-pick.
- Why: both branch from 9530231; merge keeps both histories. Result: no textual conflicts (broker_service.py and closure models touched different hunks). Prompts for baseline/generalize not touched by telemetry.

## D2 Telemetry tests fail without the implementation
- What: in a scratch `git archive` copy of the merged head (not the worktree, because an image build was reading the tree) I stubbed the emission and ran the telemetry tests.
- Go: `recordFiring` made a no-op. Failing: `TestControllerRecordsActivationBatchingAndClearingWithDispatchRelation`, `TestControllerClassifiesAnIncidentDetectorThatActivatesWhileTheResponderRuns`, `TestControllerRecordsFindingsThatNeverPersisted`, `TestFiringTelemetryDoesNotDuplicateAfterRestartOrReplay`, `TestControllerWithoutASinkStillBuildsTheClosureTimeline` (5 of the new tests).
- Python: `_detector_firing_summary` forced to "unavailable" fails `test_receipt_reports_firing_timeline_and_booleans_from_the_closure`, `test_malformed_timeline_is_reported_and_never_fails_the_receipt`; stream_curve firing branch disabled fails `test_firing_columns_report_detectors_and_booleans_from_the_receipt`; removing `BrokerClosure.detector_timeline` fails `test_closure_detector_timeline.py`.
- Why: shows the key tests constrain behavior and were not vacuous. Nothing restored in the worktree because the worktree was never mutated.

## D3 Experiment configs
- What: `sdo_codex_luna_lfint_{x,y}.toml` copied from `sdo_codex_luna_detgen_small.toml`; images `lf1`, `reflection_guidance` generalize (X) / generalize-spec (Y); order A1 A2 B1 A3; fresh handoff reflection kept.
- Alternatives: one config with env override (rejected, guidance lives in agent_config and the snapshot should record it).
- Seeds: a private copy of `/mnt/data/shli/detgen-runs/seeds/lifecycle-stream` per arm so neither arm can mutate the shared seed.

## D4 Launch under load
- Coordinator asked: stagger arms when load avg > 20. Load at 20:54 was 19.15 (25 / 22.6 over 5/15 min). Arm Y launched first (20:55, cluster lf-w offset 31); arm X launched at 20:59 when load had fallen to 8.3 (1-min) to spread cluster creation/image load. Load samples in /mnt/data/shli/lfint-runs/load.log.
- Images lf1 built from merge commit 1d... (see report); full unit suite (sdo, benchmarks, controller) exit 0; Go sdk/core/runtime ok; format/check_errors clean.

## D5 Results and gate decision (live, 2026-09-30, pipelines 20260930_205439 (Y) and 20260930_205846 (X) under third_party/sregym/logs; tables in /mnt/data/shli/lfint-runs/table_{x,y}.json)
- Both arms completed 4/4 stages with strict receipts; no usage/rate-limit errors; no harness timeouts (no rerun needed). Load samples: 19.2 at Y launch, 8.3 at X launch (X launched 4 min later).
- Y (generalize-spec): the learned family-A detector activated before dispatch at A2 and A3 (same instant as the health detector, 0.5 s before the incident id), is in the request findings and surfaced playbooks, and in `incident_detector_states`. Its source never reads Events. FP controls clean. But B1 was graded failed (diagnosis: responder reported a transient reservation bootstrap instead of the missing ConfigMap; the learned detector did not fire at B1). The gate requires 4/4 solved, so Y does not pass as written.
- X (generalize), the reference expected to fire after dispatch, also fired before dispatch, because its A1 reflection wrote a spec/Service-based detector (Watches Event, never reads one). The late-firing failure mode was therefore not reproduced in this sample, and the arms do not discriminate.
- What / alternatives / why for the wording iteration: not done. Alternatives: use the replay tool to tweak wording. Why not: the gate failure is a B1 diagnosis verdict unrelated to detector timing (detector telemetry, A2/A3 criteria and false-positive controls all passed), and the reference arm shows no late-firing to correct; wording changes cannot be tuned on family A against a failure that did not occur. Arm Y was not rerun (rerun is tied to a wording iteration).
- Receipt caveat: `fault_injected_at` in the result CSV is stamped after the injection call returns (about 6 s after the fault is applied), so activation minus injection is about -6 s for every detector including health; activation minus dispatch is the usable comparison.
- Script `benchmarks/sregym/analysis/lfint_table.py` first used a looser Event regex and mislabeled the Watches entry as a read; fixed to the brief's own `_EVENT_READ_RE`.
- Clusters lf-w30 and lf-w31 deleted after analysis. Manual steps: private seed copies, arm launches, cluster deletes, analysis script.
- Adding `vic/feat/late-findings-pull` later: merge it into this branch, rebuild all four images together, and rerun the same two configs; its per-finding delivery events should be compared against this telemetry.
