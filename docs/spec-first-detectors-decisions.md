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
