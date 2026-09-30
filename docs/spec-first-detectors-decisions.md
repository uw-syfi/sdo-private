# Spec-first detectors: decisions log

Branch `vic/exp/spec-first-detectors` (worktree `/mnt/data/shli/sdo-worktrees/specfirst`), from `vic/exp/detector-generalization` at 9530231.

## Problem
A learned incident detector fired about 11-12 s after the health detector dispatched the incident, because its predicate required a platform Event that does not exist until ~10 s after the fault. The controller attaches a late finding only in a ~2 s pre-launch window, so the learned playbook never reached the responder at dispatch. See the late-firing analysis (scratchpad, not committed).

## Decisions

1. **Fix in reflection guidance, not in the controller.** Alternatives: hold dispatch (adds latency to all incidents), channel to the running responder (transport boundary, large), lower health persistence (weakens judge false-positive guard). Why: the cause is a reflection-time choice of evidence; invariants stay intact (`controller/runtime` untouched, detectors stay deterministic Go).
2. **New flag value `generalize-spec`, a superset of `generalize`.** Alternatives: change `generalize` (rejected: the running experiment's arms must stay byte-identical), a new flag (rejected: one axis, same plumbing). Verified: prompts for `baseline` and `generalize`, resume and fresh, are byte-identical to the base commit (dumped before and after the change and compared with `cmp`).
3. **Wording is general.** Terms used: spec, status, events, logs, metrics, snapshot, near-miss. No fault, workload, port, probe, object-kind or benchmark term. The existing leak test is parametrized over `REFLECTION_GUIDANCE_MODES`, so it covers the new mode automatically; the denylist was not extended (nothing sensible to add: "event" is a generic platform concept and is already required by the SDK reference).
4. **Brief flag `reads Event objects: yes|no`** per incident detector, generalize-spec only. Detection is a source regex for `.Events(`, `.RecentEventsFor(`, `corev1.Event` over the whole detector file. A `Kind: "Event"` watch entry is deliberately not counted: existing guidance tells detectors to watch Events so they wake up, which is not a read. Alternative: parse the AST in Go (rejected: heavier, brief must stay cheap and best effort).
5. **Plumbed** to `REFLECTION_GUIDANCE_MODES` (installer validation and broker CLI choices follow), SREGym driver choices, fastloop CLI, docs/feature-flags.md.
6. **Leak test, tests first.** Added tests for the prompt superset, absence in the other modes, brief flag, plumbing; written before the implementation.

## Offline replay validation (benchmarks/sregym/analysis/reflection_replay.py, Codex gpt-6-luna, workspace-write scratch clone)

Method: replay the creation (first-occurrence) reflection of each family from the small-stream broker ledgers (fresh session, real brief), once per guidance. Mechanical check: a Go test dropped next to the created detector builds an immediate post-fault snapshot with NO Event objects (Deployment with pod template, a not-ready Pod, Service or pending Pod; `sdktest.Snapshot`) and its near-miss (same shape, condition absent); the detector must fire on the first and stay silent on the second. The same harness fails, as expected, on the real detector the live stream learned (Event-dependent). Outputs, harness and logs are in the session scratchpad (`.../scratchpad/sf/`: `A1-*`, `B1-*`, `A2-*`, `gocheck/`).

| Family / incident | Guidance | Result | Reads Events | Fires without Events | Near-miss silent | Input/output tokens |
|---|---|---|---|---|---|---|
| A creation (first incident) | generalize | new detector | yes | NO | yes | 495k / 8.4k |
| A creation | generalize-spec | new detector | no (Deployment spec only) | yes | yes | 786k / 8.0k |
| B creation (held out) | generalize | new detector | yes | NO | yes | 586k / 7.5k |
| B creation (held out) | generalize-spec | new detector | no | yes | yes | 350k / 4.0k |
| A2 (second A occurrence, widening of the existing Event-dependent detector) | generalize | modified detector | yes | NO | yes | 474k / 3.9k |
| A2 | generalize-spec | modified detector | still reads, as corroboration only | yes | yes | 516k / 5.0k |

- 6 replays total (4 planned creation replays, plus 2 on the second A occurrence: the first B attempt used the wrong ledger suffix, 67968540, which is the A2 incident; kept as an extra data point).
- **Tuning iterations used: 0 of 2.** Family A (tuning family) passed on the first wording; family B (held out, never inspected before its replay) passed on the first wording too. No wording was changed after seeing any replay.
- Decision: no controller change and no validator check this round. Alternative considered: a broker-side validator that rejects an incident detector that cannot fire on an Event-free snapshot. Deferred because it needs a general snapshot generator; the prompt-level test requirement already produced the tests.
- Observations: the spec arm costs more tokens on A (786k vs 495k, 25 vs 15 requests; it writes and runs the no-event test) and less on B. The B spec-arm summary says its isolated test run could not be confirmed (read-only Go cache in the replay sandbox); the mechanical check above passes.
- Risk kept visible: the spec-only A predicate flags any Deployment whose numeric HTTP readiness probe port is not among declared container ports. A workload that listens on an undeclared port and is healthy would be flagged; corroboration is not required. Only a live false-positive control (unrelated faults, healthy baselines) can measure that.
