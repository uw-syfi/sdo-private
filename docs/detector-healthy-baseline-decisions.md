# Detector healthy-baseline gate: decisions

Status: 2026-10-01. Branch `vic/fix/detector-healthy-baseline` (from `vic/exp/simultaneous-composites`).

## Problem

`docs/simultaneous-composites-decisions.md`: a reflection-created incident detector (`service-selector-missing-pod-label`) fired on four jaeger services of the healthy application, opened a stale incident and failed sim-a C4; in sim-b C4 it caused 39 activations before dispatch. The detector's own tests (a positive and a near-miss case) passed, because both are authored by the same responder that wrote the predicate and never include an ordinary healthy cluster.

## What validation did before

The broker validates a proposed `.sdo` tree with `MemoryValidator`, which runs a sandbox (`ContainerSandboxRunner`, `KubernetesJobSandboxRunner`, `LocalSandboxRunner`) executing `controller.builder.check_cli test`: generate a workspace (`controller/builder/workspace.py`), then `go test ./...` and `go build`. The only cross-detector generated test is the ExternalName invariant. No healthy cluster state exists anywhere in that path: the closure carries no pre-incident snapshot, the controller does not persist its observed snapshots, and the health-detector tests are the detectors' own hand-written fixtures. So a baseline snapshot is not available and had to be plumbed.

## Decision

Follow the existing ExternalName pattern: a generated Go test in the validation workspace.

1. `sdktest.LoadSnapshots(dir)` and `sdktest.HealthyBaselineViolations(detector, snapshots)` (`controller/sdk/sdktest/baseline.go`). Snapshots are JSON files in the `sdktest.Snapshot` shape (json tags added). Any active finding is a violation; the message names the detector, snapshot file, rule, resource, summary, and the remedy (key the predicate on a state the healthy application never has). Resolved findings and detector errors are ignored (other tests own them). Empty directory or unknown JSON fields fail loudly so the gate cannot silently check nothing.
2. `BuildWorkspaceConfig.healthy_baseline`: when set, the workspace copies the fixtures to `generated/testdata/healthy-baseline` and writes `generated/healthy_baseline_test.go`, which runs every `sdk.DetectorClassIncident` detector over them. Health detectors are skipped (judge-owned; a health detector firing on the baseline is a different question).
3. `check_cli test|draft-test --healthy-baseline DIR` (relative to the app root, must stay inside it).
4. Sandbox runners take `healthy_baseline=` (relative path, no absolute or `..`) and pass the flag; container validation identity gains a `-healthy-baseline` suffix so cached verdicts from ungated runs are not reused. Broker CLI: `--healthy-baseline-dir`.

Default off: with the flag unset the generated workspace, container command, Job args and identity are byte-identical to before. Detector runtime is unchanged (the check is test-time only), `controller/runtime` is untouched, and no problem-specific knowledge is added. Rejected alternatives: a new `.sdo` artifact class for the baseline (the commit broker's ownership model would need a sixth class), and deriving a baseline from the detector's own test cases (same author, same blind spot).

## Tests

Go: `controller/sdk/sdktest/baseline_test.go` (loading, sorting, empty and malformed baselines, noisy vs quiet detector, resolved findings). Python: `tests/unit/controller/builder/test_checker.py` (workspace with and without the flag, bad baseline paths, end-to-end `check_cli test` rejecting a detector that flags every selected Service on a healthy fixture and accepting one that flags only unbacked selectors, default behavior unchanged, path escape rejected), `tests/unit/sdo/operational_memory/test_sandbox.py` (flag absent by default, passed for all three runners, bad paths, identity), `test_broker_service.py` (broker wiring). All failed before the implementation. `tests/unit/test_architecture.py::test_cross_package_imports_go_through_facades` fails on the base branch too (benchmarks facade imports) and is unrelated.

## What a follow-up must wire for live runs

Nothing produces the snapshots yet; the gate is inert until then.
1. Capture: at the all-clear health baseline (the fastloop/persistent stage already waits for it, or any quiet controller evaluation) dump the namespace's resources into `sdktest.Snapshot` JSON (a small `kubectl get ... -o json` converter, or a controller `--dump-snapshot` flag in `controller/core`, which already builds `kubernetes_snapshot`). Record several snapshots over time if flapping matters. Do it inside the benchmark adapter or install path, not `controller/runtime`.
2. Place: the validated tree is the worktree, so copy the files into the worktree (for example `.sdo-baseline/healthy/`, untracked or git-excluded so the commit broker never treats them as owned artifacts) before validation; the broker service is the natural place.
3. Enable: add `--broker-arg=--healthy-baseline-dir=.sdo-baseline/healthy` in the controller install (`sdo/controller_install`, the same place that passes `--validator-mode`), behind an opt-in `ControllerInstallConfig` field and SREGym `agent_config.sdo_codex` key.
4. Reflection feedback: the rejection text already flows back to the reflector as validation feedback, so a rejected detector is corrected within the existing retry.

Caveat: a baseline captured from one application state can miss shapes only present later (for example during rollouts); capturing a few snapshots reduces that. The gate catches false positives on the healthy state, not on every transient state.

## Rebuild needed (not done)

`sdo-detector-validator` image (new Go code in `controller/sdk/sdktest`, Python in `controller/builder`; `controller/Dockerfile.validator`). Controller and responder images only if the `sdo` Python package (`sandbox.py`, `broker_cli.py`) is baked into them. Existing images keep working unchanged since the flag defaults off.
