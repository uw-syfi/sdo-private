# Repair attribution: robust to responder clock drift

Branch `vic/fix/repair-attribution-clock` (from `vic/exp/mixed-integration`), 2026-10-02. The existing
`vic/fix/repair-attribution` branch is unrelated earlier work (F8/F17), so this one has a different name.

## Problem

The phase A cold `network_policy_block` run deleted the NetworkPolicy at 07:57:07.5. Health cleared at
07:57:08.8 and the link finding fired 7.2 s after injection, so the repair worked. The responder then wrote the
action's `started_at` from a clock read at 07:57:17. `verify_diagnosis` credits a cause only through a successful
action that started by `health_cleared_at`, so the cause was `unattributed`, the outcome `external_recovery`,
and reflection was skipped: nothing was learned. The prompt-level fix (`aabf8b0d`, "take times from `date -u`")
asks the model to be accurate. That depends on a self-reported clock, so it is fragile.

## Decisions

1. **Keep the strict rule; add a bounded, anchored exception.** An action that started by `health_cleared_at`
   is credited as before. An action whose start trails it is credited only when all of these hold:
   - it is successful and lists the object in `resources` (or its `target`);
   - the start is at most `REPAIR_CLOCK_SKEW` (30 s) after `health_cleared_at`;
   - the start falls in the responder session `[dispatched_at, responder_completed_at]`, widened by the same 30 s;
   - the object is in the dispatch-time state diff and absent from the closing diff, so the controller itself saw
     it changed at dispatch and gone by closure.
   Credit is limited to those objects, so an action that also touched unrelated objects does not back causes
   about them.
   - Alternatives: (a) a flat tolerance on `started_at` with no anchor: it would credit a no-op claim after any
     external revert within 30 s, with no controller evidence; rejected. (b) Controller-observed removal times
     (add `last_observed_at`/`absent_at` to `ObservedStateChange`): the Go closure, state store, fixtures and
     receipts would change for a bound that still cannot tell a real delete from an `--ignore-not-found` no-op,
     since both fall in the same window; rejected for now (see Residual). (c) Parse the Codex command log for
     mutation timestamps: no harness-side command log reaches the broker, so this needs new plumbing; rejected.
2. **Python only; no Go or schema change to the closure.** The closure already carries `dispatched_at`,
   `responder_completed_at`, the dispatch diff and `final_state_changes`. `verify_diagnosis` gains two optional
   keyword arguments, `dispatched_at` and `responder_completed_at`; without them the strict rule holds, so older
   callers and records behave as before. `derive_outcome` and the SREGym receipt recomputation pass them.
3. **Make the correction visible.** `RepairAttribution.clock_skew_corrected` lists the credited action IDs that
   needed it and the `reason` says so. It defaults to `[]`, so old records validate. The analyze-experiment
   schema reference and `docs/architecture.md` describe it.
4. **Responsibility stays with the controller and broker.** Nothing here lets the responder grant itself credit:
   the controller's diffs and session times decide.

## Tests (red first)

- `test_diagnosis.py`: the exact timeline (+8.5 s, policy gone, in session) is attributed and marked
  corrected; an on-time repair is not marked; +90 s (beyond the tolerance), a report after the session ended,
  no session window, a fault still present in the closing diff, an object outside the dispatch diff, a failed
  action, and the F8 case (the responder touched something else while someone else reverted the policy) all stay
  `unattributed`.
- `test_outcomes.py`: the same timeline gives `SUCCESS` (so reflection may learn), not `EXTERNAL_RECOVERY`.
- `test_driver.py`: the SREGym receipt recomputation credits it (`recovery_attribution = responder`); it fails
  without passing the session times.

## Residual

A responder that reports a no-op delete (for example `--ignore-not-found`) within 30 s after an external revert of
an object in the dispatch diff, inside its own session, is credited. Nothing SDO records can tell that apart from
a real delete, the same residual as F17's "touched the same object" note. The window is small, requires the action
to name the object, and the object must have been in the dispatch diff and gone by closure.

## The prompt guidance (`aabf8b0d`)

It is no longer required for correctness, since a skew up to 30 s is absorbed. It still makes receipts accurate
for analysis, costs a few prompt lines, and covers skew above the tolerance. Keep it until the confirming run
shows the correction firing on real receipts; it can then be dropped without a behavior change inside the
tolerance.
