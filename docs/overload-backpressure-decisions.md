# Overload / backpressure decisions

Robustness thread #2. This note records where the long-running controller admits,
dispatches, and drains incident work; where resource pressure previously produced
silent slowdown instead of explicit backpressure; and the deterministic admission
model that replaces the race. It builds on the incident lifecycle event stream
(#14) rather than forking a parallel telemetry path.

## Motivation

The n=3 variance study found that under *sustained host load* the system degrades
in correctness, not only in speed: the incident drain / worktree close-out path
produced strict-receipt anomalies (`completed != true`, `remaining_worktrees != []`)
that spread from composite faults to single faults (i05/i06) only under load, and
composite diagnosis-characterization variance tracked host load. The close-out
worktree *leak* was already fixed at source (#11 adapter reap + broker primitive +
timing; #12 controller durable `ReleaseEffect`). What remained is that the
controller had **no explicit backpressure**: when resources were scarce it slowed
silently and timing races widened, rather than shedding or queuing deterministically.

## Where incident work flows today

`controller/runtime` is a single-incident, event-loop-driven controller
(`run.go` `RunWithOptions`). One incident is open at a time (`incidentOpen`), and
every external side effect is executed one at a time by `executePendingEffects`,
each spawning one goroutine whose completion returns on a buffered channel
(`effects.go`, `broker_effects.go`). The concurrency of broker/dispatch effects is
therefore already bounded (one dispatch *or* workspace, one closure *or* ack, one
release in flight).

The **admission point** is `Controller.dispatchReady` (`controller.go`): when a
finding batch is ready, no incident is open, and no closure is pending, it drains
the batch, builds the `IncidentRequest`, sets `incidentOpen = true`, and moves
`dispatchState` to `pending` / `workspace_pending`. Nothing here was load-aware:
a ready batch always opened immediately and raced the (possibly congested)
close-out/drain of the previous incident and the stranded-worktree release queue
(`pendingReleases`), which is itself unbounded.

## Model: load-aware admission with explicit deferral (never shed)

We add a deterministic admission gate in front of the open transition. When the
system is over a pressure threshold, a ready batch is **deferred, not dropped**:
the incident is opened (it gets an identity and a `PhaseOpened` event, and later
findings still coalesce into it via `attachBeforeLaunch`) but held in a new
`dispatchState = "admission_deferred"` before any worktree is prepared or responder
dispatched. We **defer rather than shed** on purpose: dropping an incident would
lose a real fault and would re-introduce exactly the strict-receipt/worktree
anomalies this thread exists to remove. Backpressure here means *hold and
re-check*, not *discard*.

Two independent pressure terms, each with its own high/low watermark hysteresis so
admission cannot flap:

- **Host load** — an injected `LoadGauge` reading (production: `ProcPressureGauge`
  over Linux PSI `/proc/pressure/cpu` "some avg10", a 0–100 percentage). The gauge
  is optional; absent, this term is zero.
- **Release backlog** — `len(pendingReleases)`, the controller's own stranded-worktree
  reap queue. This term is pure controller state, needs no host introspection, and
  directly ties admission to the congested drain path the n=3 study implicated: a
  deep reap backlog defers *new* incident admission instead of racing it.

A ready batch is deferred when either term is at/above its high watermark. A
deferred incident resumes only when **both** terms are below their low watermarks.
The decision (`AdmissionConfig.assess`) is a pure function of (currently-deferred,
load sample, backlog) and is unit-tested directly. While deferred the controller
re-samples every `RecheckInterval` (scheduled through `NextWake`, no wall-clock
spin). The load sample is taken outside `c.mu` so a gauge read never holds the lock.

Determinism and testability: the gauge and the clock (`c.now`) are injectable, so a
seeded test scripts a load trajectory against a fake clock with no wall-clock
flakiness, exactly like the chaos/replay harness. `ProcPressureGauge` takes a
configurable path, so even the production reader is driven from a temp file in
tests. A gauge read error fails *open* (admit) and is reported, so a `/proc` hiccup
never wedges incident handling.

Default **off**: `AdmissionConfig.Enabled == false` (the zero value) admits every
ready batch immediately, byte-for-byte as before. No existing run changes behavior
unless an operator sets the watermarks. Config is validated in `NewController`
(`AdmissionConfig.validate`, raising on negative or inverted watermarks, a
non-positive recheck interval, or "enabled with no high watermark").

## Observability: reuse the #14 lifecycle stream

Backpressure is observable and replayable through the same durable, bounded,
content-addressed lifecycle stream (`durableEventStream`), not a new channel. Two
phases are added:

- `admission_deferred` — a ready batch was held because load / backlog reached a
  high watermark. Carries `load_pressure`, `load_threshold`, `release_backlog`, and
  a `reason` (`load_above_high_watermark` or `release_backlog_above_high_watermark`).
- `admission_resumed` — a deferred incident cleared both low watermarks and
  proceeded to dispatch. Carries the clearing `load_pressure` / `release_backlog`
  and `reason = below_low_watermark`.

Both are keyed to the incident id, so each `(incident_id, phase)` pair is emitted at
most once and the event id is `sha256(incident_id, phase)` — a crash-replay between
emitting and persisting reproduces the id and the sink drops the duplicate, exactly
like every other lifecycle phase. They join the per-incident timeline in
`ReconstructIncidentTimeline` and sit by rank between `incident_opened` and
`workspace_prepared`. No field is benchmark- or transport-specific:
`controller/runtime` stays transport-neutral and these are controller-internal load
facts only; no detector/LLM is involved.

`dispatchState = "admission_deferred"` is a stable (no-goroutine) state, so it
persists through `ExportState`/`RestoreState` unchanged; a restarted controller
re-samples and either stays deferred or resumes, re-emitting the same (deduplicated)
events.

## Scope / deferred

- Production enablement is via controller flags (`--admission-*`), matching the
  flag-only precedent of `--lifecycle-telemetry-path`. Wiring the knobs into the
  `ControllerConfig` proto + `controllerConfigToArgs` is a follow-up, kept out of
  this PR to avoid a proto regen in a robustness change.
- We did **not** touch the fenced `verify_diagnosis` / contradiction / red-herring
  path, and nothing here couples to SREGym.
