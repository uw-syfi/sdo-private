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
