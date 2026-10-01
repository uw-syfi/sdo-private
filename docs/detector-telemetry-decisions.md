# Detector firing telemetry: decisions

Branch `vic/feat/detector-firing-telemetry` (from `vic/exp/detector-generalization`). Goal: record durably, per incident, whether and when each detector fired, so a run can say whether a learned incident detector fired before the responder was dispatched. Each entry is what / alternatives / why.

1. **Storage: a JSONL file on the repository PVC under `/workspace/.sdo-runtime/telemetry/`.**
   Alternatives: (a) append to the state ConfigMap; (b) `.sdo/` memory (forbidden); (c) a separate ConfigMap per record; (d) files in the application repository worktree.
   Why: the state ConfigMap is capped at 1 MiB and is rewritten wholesale on every save; a stream there would compete with closure state. `.sdo/` is the five artifact classes. The repository worktree would dirty the git tree the broker inspects. `.sdo-runtime/` already holds usage logs, is on the PVC that survives controller restarts, and is already exported by the adapter.

2. **Flag `--firing-telemetry-path`; default on in job mode, off in local mode, `off` disables.**
   Alternatives: always on; require an explicit flag from the installer.
   Why: no installer or supervisor change is needed for production job mode (`sdo.controller_install` constants only mirror the path, asserted by a test), while local development and unit tests stay hermetic. Sink open failure is reported to stderr and telemetry is skipped, because telemetry must never stop the controller.

3. **Events: `activated`, `cleared`, `not_persisted`, plus `batched`.**
   Alternatives: only activated/cleared; put the final relation on the activation record.
   Why: the batcher debounces, so at activation time the finding is usually not yet in an incident and the relation is unknowable. Rewriting an append-only record is wrong; a later `batched` record (with the incident id) settles `before_dispatch`. `not_persisted` was cheap (one extra branch in `FindingStateTracker.Observe`) and is the direct answer to "flagged but never persisted".

4. **Dispatch relation rules.** In the request's findings: `before_dispatch`. Incident open and responder launched (`incident_dispatched_at` set), finding not in the request: `after_dispatch`. Otherwise `no_incident`. An incident that is open but not yet launched, with a finding that was not attached (below the batching severity), is classed `no_incident` with the incident id still filled; I chose this over a fourth relation value to keep the requested three-value vocabulary.

5. **Restart idempotency via deterministic event ids plus sink-side dedup.**
   Alternatives: emit only after the state is durably saved (exactly-once needs a transactional outbox); a high-water mark file; timestamps as ids.
   Why: the loop saves state after stepping, so a crash between emitting and saving replays the transition. Ids hash detector, fingerprint, kind and a per-finding transition counter (`activations`, `clears`, `not_persisted` on `FindingState`, persisted in the ConfigMap), so the replay yields the same id and the file-backed sink (which loads ids from the live and rotated file on open) drops it. A normal restart restores the counters and emits nothing. `batched` ids hash the incident id instead. Timestamps or iteration numbers differ on replay, so they cannot be part of the id.

6. **Stale stream vs fresh state.** If no durable state is restored (`RestoredFromState()` false), the existing stream is archived to `.prev`. Why: ids derive from state counters; a new lifecycle restarting at counter 1 would otherwise be silently deduplicated against an old stream. One archive is kept.

7. **Bounds: 4 MiB live file rotated once to `.1` (max 8 MiB), torn last line repaired on open.** Alternatives: compaction (rewrite keeping only the newest N); time-based rotation. Why: records are append-only evidence, compaction would need rewrites and atomic renames of a file the adapter tars concurrently; single rotation is simple and crash-safe (rename is atomic). Older-than-`.1` records are dropped; in a measured experiment (tens of incidents) this is orders of magnitude below the bound. Each record is fsynced; transitions are rare (a few per incident), so the loop cost is negligible.

8. **Evaluation iteration is durable (`RuntimeState.evaluation_iteration`).** The existing `controller_iteration` printed by `run.go` resets on restart and is stdout only; telemetry counts evaluation passes that took a snapshot and survives restarts. Both fields are `omitempty`, so older states load unchanged.

9. **Closure `detector_timeline` kept in controller state, not derived from the stream.** Alternative: reconstruct from the stream at closure. Why: the stream is optional and bounded, the closure must be correct without it, and state already carries the equivalent data for the pending closure across restarts. Bounded to 64 entries (oldest cleared evicted first). The timeline is reset when the closure is cut; entries that cleared before an incident opened and did not join its batch are pruned as unrelated blips.

10. **Derived booleans.** `incident_detector_fired_before_dispatch` / `..._after_dispatch`: any non-health entry with that relation. `no_incident_detector_fired`: the timeline is non-empty and holds only health detectors. A detector with no class counts as an incident detector. The three are computed in Go (single source of truth) and copied, not recomputed, by the adapter; the stream fallback in `stream_curve` can only compute before/after and leaves `no_incident_detector_fired` unknown.

11. **Python typing.** `DetectorTimelineEntry` is a `ContractModel` (`extra="forbid"`) in `sdo.contracts`, re-exported from `sdo.operational_memory` because `tach.toml` forbids `benchmarks.sregym.adapter` from importing `sdo.contracts`. `BrokerClosure` gains default-valued fields, so older closures still validate.

12. **Receipt and stage results.** The strict receipt gets `detector_firing_available`, `detector_timeline`, and the booleans (null, never false, when the closure predates telemetry); a malformed timeline is recorded as `detector_timeline_error` and never fails the receipt (diagnostic evidence). The raw stream rides the existing runtime-artifact tar export (`sdo_runtime/telemetry/`), and `detector_firings.jsonl` (rotated file first) is written beside the receipt. The Go side has no SREGym knowledge; the benchmark relays stay behind the adapter.

13. **Did not change `.sdo/outcomes.jsonl`.** The telemetry lives in the closure and the stream; the outcome schema, memory validator and ownership rules are untouched.

14. **Not done.** Moving the stream into the controller image build is not needed. No cluster run, LLM call or image build was performed. The two pre-existing `E501` ruff findings in `benchmarks/sregym/analysis/detgen_table.py` and `reflection_replay.py` on the base branch were left alone. A permission classifier denied one read-only inspection command (looking at how the broker ledger serializes the closure); I did not retry it or work around it. The Python tests assert the closure model round trip instead, and the ledger copies the closure dict the adapter already reads.
