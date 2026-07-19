# Common SDO experiment failure patterns

## Incomplete evidence chain

**Signature:** benchmark output reports success but the strict receipt is missing, invalid, or cannot be correlated to an outcome and broker ledger.

Check incident IDs, commit hashes, final detector state, independent verification, acknowledgement, cleanup, and remaining worktrees. Report benchmark success and production completion separately.

## Lifecycle provenance mismatch

**Signature:** deployer commit or topology fingerprint differs from the current application workspace, judge sessions are reused unexpectedly, or the objective digest differs from `goal.md`.

This indicates stale or fabricated bootstrap memory. Check whether a pipeline lifecycle seed was copied and whether reuse validation ran against the correct source revision.

## Repeated deployment rejection

**Signature:** bounded source-deployment attempts receive the same verifier feedback or fail to create an attributable commit.

Look for unchanged Git HEAD, missing role trailer, Kubernetes rollout errors, agent claims unsupported by cluster state, and verifier feedback that the next attempt ignored.

## Detector quality or build failure

**Signature:** generated Go fails manifest validation, compilation, tests, or near-miss behavior.

Check ownership/class, objective digest, watches, namespace handling, source-backed resource coverage, nondeterministic dependencies, and whether tests merely restate implementation details.

## Finding-state instability

**Signature:** incidents fire or clear unexpectedly, duplicate dispatches occur, or restarts change behavior.

Inspect firing/clearing thresholds, debounce state, snapshot versions, durable controller state, leader transitions, and incident idempotency keys.

## Responder detour or thrashing

**Signature:** the responder performs broad exploration, repeats hypotheses, changes unrelated source, or alternates between fixes without improving detector/health evidence.

Compare trajectory actions with findings, surfaced playbooks, architecture, and validator feedback. Count proposals and rejected commits rather than only model turns.

## Ownership or broker rejection

**Signature:** a repair appears plausible but the broker rejects it.

Check edits to human goals, architecture, health detectors, prior outcomes, or paths outside the allowed worktree; missing provenance; dirty or uncommitted state; detector validation; and stale branch heads.

## Reflection without learning

**Signature:** an outcome commit exists but reflection is absent when required, uses a different session, produces no validated memory commit, or generalizes beyond evidence.

Correlate classification, health verification, responder session ID, outcome commit, reflection commit, accepted detector paths, and controller-update rollout.

## Benchmark leakage

**Signature:** lifecycle, detector, controller, or responder evidence contains fault labels, verdicts, submission APIs, or hidden benchmark metadata.

This invalidates the production path. Benchmark transport belongs only in the SRE Gym adapter.

## Healthy evidence chain

| Layer | Expected evidence |
|---|---|
| Lifecycle | Distinct fresh sessions, current source commit/topology, validated objective detector |
| Controller | Persistent finding history, one correlated incident, durable state |
| Responder | Isolated worktree, structured result, attributable proposal |
| Broker | Ownership checks, independent verification, committed outcome |
| Reflection | Same-session proposal when applicable, validated memory commit, correlated rollout |
| Benchmark | Explicit diagnosis and mitigation success plus one valid strict receipt for SDO adapter runs |
