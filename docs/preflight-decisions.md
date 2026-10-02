# Experiment preflight: decisions

Branch `vic/feat/experiment-preflight`, from `vic/exp/mixed-integration` (`ce7663db`), 2026-10-02.

## Why

The mixed-stream confirming run lost about two hours to integration bugs that a fast no-LLM check would have caught:

1. Fastloop's `run_or_reuse_lifecycle` never passed `validator_image`, so the host-side lifecycle validated in a stale image and the health judge's `links.yaml` was dropped.
2. The Python launcher `sdo-detector-check controller` did not define `--closeout-state-gate` although the installer passes it; every controller pod exited with `unrecognized arguments`.
3. The deferred fault gate injected before the link prober's first dial (fixed in `ce7663db`).
4. `--inject-before-resume` injects before any link baseline exists, so an isolating NetworkPolicy cannot be detected.
5. The stale `sdo-detector-validator:v0.1.0` fallback.

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | Extend the existing launch preflight (`benchmarks/sregym/runner/preflight.py`) with new checks and share them with fastloop, instead of writing a second preflight. | A standalone `sdo preflight` command. | The runner already runs a preflight on every experiment and records it in the run manifest. A second one would drift. |
| 2 | New checks live in `runner/launch_contract.py` as plain functions returning `Finding`s over two small host methods; `preflight.py` and `fastloop/preflight.py` wrap them. | Put them in `preflight.py`. | Avoids growing a 1100-line module, avoids a circular import, and lets fastloop use them without the experiment-config machinery. |
| 3 | `controller_launch_flags(**features)` in `sdo.controller_install` computes the flags a launcher passes from the installer itself, by rendering the Job for a placeholder config. The runner depends on `sdo.controller_install` (tach). | Duplicate a feature-to-flag table in the runner. | The table would drift from the installer, which is exactly the bug class. Benchmarks may import the production API. |
| 4 | The flag contract is checked at three layers in a unit test: installer args against `sdo-detector-check`'s parser, forwarded Go command against the flags declared in `controller/runtime/run.go`, and broker args against the broker parser. `_controller_once` now builds its Go command in `runtime_command` so the command line is testable without a build. | Test only the Python parser. | The Go layer is where an unknown flag would still kill the pod. Removing `--closeout-state-gate` from the parser fails 10 tests. |
| 5 | The image checks run inside the image with `docker run --entrypoint` (`image_output`): the controller launcher's `--help` and the validator's traffic SDK sources. The validator check compares JSON and YAML field names with the checkout's `controller/sdk/traffic` instead of hashing files. | Hash the whole SDK; trust image tags. | Field names are what the strict workload decoder rejects; a hash would fail on unrelated edits after a build. Both real stale images (`v0.1.0`) are caught in 17 s. |
| 6 | `controller-flags` requires only the flags for the features the run enables (closeout gate, follow-ups, late findings, healthy baseline). The image-only CI mode checks all features. | Require every flag always. | A stale image missing a flag nobody uses is not a failure for this run. |
| 7 | A check can be `unknown` (reported, never failing) for risky-but-legal combinations. | Add a `warn` status. | `unknown` already means non-failing in the report, manifest and validity logic; a new status would touch all three. |
| 8 | Quota no longer holds a run by default (`max_quota_used_percent` default 100, only a fully used window fails). | Keep 85. | The user said to ignore quota checks (2026-10-02). The limit is still settable. |
| 9 | Host load above `SDO_PREFLIGHT_MAX_LOAD` (default 25) fails, with `SDO_PREFLIGHT_WAIT_LOAD_SECONDS` to wait. | Warn only. | Load above about 25 produced `health` timeouts and not-Ready pods that were misread as product failures; failing early with a wait option is cheaper. |
| 10 | Fastloop runs the preflight at `up` (images, seed, lane, load, auth) and again at `run` (images against that run's features, lint). `--no-preflight` opts out; `SDO_PREFLIGHT=warn` warns. | Only at `up`. | The features that decide which flags matter are known only at `run`. |
| 11 | The "deferred injection requires a prober-ready wait" rule is not a preflight check. | A source grep for the wait. | The behavior is a code fix with its own tests (`ce7663db`); a grep would be brittle. The lint covers the config-level cause (`--inject-before-resume`). |
| 12 | Existing unit tests that call `_up` through `main` pass `--no-preflight`. | Leave them. | Without it they probed the real Docker host. |

## Limits

- Checked only against the existing `mi2` and `v0.1.0` images with read-only `docker run`; no cluster was started.
- The preflight does not detect a validator SDK that differs in behavior but not in field names.
- `tests/unit/benchmarks/sregym/agents/crucible` breaks xdist collection and was not run; some tests need `third_party/sregym` populated.
