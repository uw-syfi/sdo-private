# Merge to main: composite-stream, manifest guard, SREGym `sdo` branch

Integration of the stacked SDO experiment branches (based on the pre-phase1 main, `8cadce15`) into `origin/main` (`69af114e`, 246+ commits ahead of that base).

## What was merged

| Source | Result |
| --- | --- |
| `vic/exp/composite-stream` (`d5531ca6`) | merged; it already contains healthy-baseline-ab, healthy-baseline-live, detector-healthy-baseline, late-findings-pull, redispatch-residual-findings, nfault-composites, composite-learning-curve, simultaneous-composites, cold-c2-selector-learning, hand-composites and stream-learning-curve (verified with `git merge-base --is-ancestor`) |
| `vic/fix/source-repair-manifest-guard` (`e411ae31`) | merged separately (not in composite-stream) |
| `third_party/sregym` | pinned to the SREGym fork branch `sdo` |

Not merged: `vic/docs/composite-stream-proposal` (docs only, not requested). `docs/hand-composites-decisions.md` was already tracked and identical to the copy in the composites worktree.

## SREGym `sdo` branch

`origin/main` pinned `e0803ea0` (`vic/fix/agent-proxy-exec`), which is nine commits ahead of `vic/feat/fastloop-integration` (`e094984a`), so `sdo` is based on `e0803ea0`, a superset. The composite branches `vic/feat/composite-problems` and `vic/feat/fastloop-composite-faults` were already contained in it; `vic/exp/nfault-composites` and `vic/exp/hand-composites` are contained in the local-only `vic/exp/composite-stream` tip (`03b1df58`), which was merged. One conflict (a formatting-only line wrap in `inject_virtual.py`) kept the base form. The registry imported two different `composite_factories` under one name; the hand-registered one is now `hand_composite_factories`.

## Conflict resolutions

- Both sides' features are kept wherever they were independent: the detection-timeout and detector-review-stop options of `StageInputs`; `sdo incident status` and `sdo incident late-findings`; the state-change diff and `FollowUp` on `IncidentRequest`; closure fields for state changes and the detector timeline; firing telemetry, follow-up responders and the healthy-baseline gate alongside the gate-confirmation, helper cleanup, and dispatch-retry policies.
- Defaults follow main: `DEFAULT_REFLECTION_SESSION` (fresh) replaces the branch's hard-coded `resume`; the responder RBAC/exec wording follows main (the forbidden-verbs constant no longer exists), keeping the branch's "restart pods blocked on a restored dependency" playbook rule.
- `PausedWake`: main already had it; the duplicate was removed. Main's `executePausedEffects` is used while paused.
- `FindingStateTracker.Observe` takes `now` on main (MinDuration); the branch's firing telemetry tests were updated to pass it.
- Timeline entries encode `surfaced_playbooks` as `[]` (main forbids null collections); Go contract fixtures `closure_repaired.json` and `closure_own_edit.json` were regenerated.
- `Snapshot` (sdktest) keeps the branch's JSON tags and `SourceName` plus main's `Traffic`.
- Tests: add-add conflicts keep both sides. `test_persistent.py` was rebuilt from main's version plus the branch's additions; the branch's obsolete RBAC-forbidden-verbs test was dropped.
- Lint: the one-off experiment analysis scripts from the branch violated style rules; `pyproject.toml` ignores style-only rules for `benchmarks/sregym/experiments/*/*.py`, and one f-string that needed Python 3.12 was rewritten.
- Facades: the merged fastloop and replay code imported `benchmarks.sregym.adapter.persistent` and `sdo.agent_runtime.responder.reflection` directly; they now use the package facades. `DetectorReviewRequiredError`, `PersistentControllerError` and `wait_for_maintenance_ack` (formerly private `_wait_for_maintenance_ack`) are exported from `benchmarks.sregym.adapter`.
- Main's persistent-stage timeout tests that use a stuck responder now pass `stop_on_detector_review=False`, since the merged default stops the stage at once for detector review. The generalization leak-scan test excludes the shared responder RBAC description.

## Checks (run in the integration worktree)

- `scripts/format_code.sh`, `scripts/check_errors.sh` (ruff, tach): pass.
- Go: `controller/runtime`, `controller/sdk`, `controller/core` `go vet` and `go test ./...`: pass.
- Python `tests/unit`: 2518 tests pass. Eleven tests that read the SREGym checkout (assurance-suite registry, kind image preflight, Codex pin) cannot run in a worktree without `third_party/sregym` populated; they pass (71 passed) against the pushed `sdo` branch checkout.
- `tests/integration`: 7 skipped (need external tooling).
