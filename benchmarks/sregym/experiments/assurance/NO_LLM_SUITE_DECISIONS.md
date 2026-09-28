# No-LLM assurance suite: decisions

Branch `vic/feat/no-llm-assurance-suite`, based on `vic/exp/feedback-loop-e2e`
(`104162b`). Every entry was decided autonomously unless marked
**coordinator-directed**. No LLM or Codex call is made anywhere in the suite.

## N1. Drive the production controller, not a hand-built harness

- **Decision.** The suite compiles the seed's `.sdo` with the production
  builder (`BuildWorkspace`, `go build ./cmd/controller` and `./cmd/prober`)
  and runs that binary through `run.go`, exactly as production starts it.
  Only the two agent seams are replaced:
  - the responder is a scripted `--dispatcher-mode local` executable
    (`assurance/responder.py`) that publishes the incident request in a spool
    and returns the result the suite writes;
  - the broker is the production `BrokerService` + `CommitBroker` with no
    reflector (`assurance/broker.py`); `BrokerService` skips reflection when
    it has none.
- **Alternatives.**
  - Reuse the earlier ad hoc smoke driver (scratchpad `combined/main.go`),
    which built `StateTracker`, the detectors, the CLI and the cleaner by hand.
    Rejected: it passed while production was broken, because it called
    `StateTracker.Start` with a long-lived context and `run.go` did not
    (SDO bug 2, fixed in `13d5613`). An assurance suite must go through the
    same wiring as production.
  - Install the controller in-cluster with `sdo.controller_install` and swap
    only the responder image. Rejected: the installer hard-wires Job-mode
    dispatch to the LLM responder and the reflecting broker; the scripted
    responder/chaos workstream covers the Job path.
- **What is therefore real:** detectors, prober and traffic engine, the state
  tracker's informers and 2-minute settle, incident batching and dispatch,
  helper cleanup, the closure gate, the broker's outcome commit with
  diagnosis verification, `sdo incident status`, and the SREGym submission
  gate. **What differs from production:** the controller runs on the host with
  the admin kubeconfig (so RBAC gaps would not show; `state_baseline_unobserved_kinds`
  is recorded), and it reaches the prober through a supervised
  `kubectl port-forward` (restarts are counted in the results).

## N2. The prober pod

- The prober runs as `sdo-prober` in `hotel-reservation-sdo` with the
  manifests of `controller/runtime.ProberPod` (no token, read-only root,
  dropped capabilities, 250m/128Mi limits, egress only to the app namespace
  and DNS), its binary published on the `sdo-application-repository` PVC at
  `.sdo-prober/<sha16>/` like the builder does.
- **Why not let the controller create it** (`--prober-binary`): the controller
  would then address the pod IP, which the host cannot route to on kind.
  `kubectl port-forward` enters the pod's network namespace, so the
  production NetworkPolicy stays unchanged.

## N3. The seed

- The seed is lifecycle commit `30e023d` exactly (from the live e2e: three
  scenarios, `health` and `verify` workloads, detectors `health-objective`,
  `service-endpoints`, `traffic-health`). Its `.sdo` is checked in under
  `seeds/hotel_reservation_30e023d/operational_memory` (not `.sdo`, which
  tools would discover as an app root) and rebuilt on top of SREGym's hotel
  source by `assurance seed`. The rebuilt tree hash equals the original
  (`a2e00d5`), so the suite does not depend on an earlier run's scratch
  directory.

## N4. Faults and fixes

- Faults are SREGym problems injected and recovered by the fast-loop fault
  driver, so the suite tests the same faults the live evals use. The
  **correct fix** is SREGym's own recovery.
- **Wrong fixes** (scripted, plausible, leave the fault in place):
  1. the decoy story: run SREGym's own Mongo privilege-restore scripts from
     `failure-admin-geo`/`-rate` (as the earlier smoke did);
  2. restart the most tempting nearby Deployment.

  After each one, `sdo incident status` must exit 1 and the submission gate
  must exit 4.
- **Composite faults.** The SREGym fast-loop worker now composes faults
  (`inject compose=true`, `recover fault=i`, a `composition` oracle; SREGym
  `a1c00a3e`), and `SregymFaultDriver` exposes `inject_composite` and
  `recover_fault`. A composition may not list a problem twice or have two
  faults change the same object, because SREGym keeps one backup file per
  service and a second fault would overwrite the first's backup.
- **Ordering inside one run.** The responder stays open while the suite
  applies wrong fixes, partial fixes and the correct fix, then returns a
  result citing the state change and the detectors that fired. Helpers are
  created before it returns, so the controller's cleanup at responder
  completion is what removes them.

## N5. Bounds (fixed before the first run)

| Bound | Value | Why |
|---|---|---|
| Injection end → incident opened | 30 s | Earlier smoke: first finding in 1.3–2.2 s; firing needs 2 evaluations plus a 0.5 s debounce |
| Correct fix end → status 0 | 60 s | Recovery already waits for rollouts; a verify burst takes about 3 s |
| Correct fix end → controller verified | 90 s | Clearing needs 2 clear evaluations of every health detector (intervals 10–30 s) |
| Responder exit → helpers gone | 15 s | Cleanup has a 10 s timeout; the earlier smoke took 10–13 ms |

Detection latencies are measured from the **start** of the SREGym injection
call. The call itself takes 4–6 s (it waits for rollouts), and the shakedown
showed the detectors firing before it returned, so latencies measured from its
end were negative. Measuring from the start gives an upper bound on the true
latency and makes the 30 s bound stricter.

The traffic prober's own first finding is reported per fault. For
network_policy_block it is a **known gap** (coordinator-directed, below) and
does not fail the run.

## N6. Baseline absorption check (the 2-minute rule)

- The healthy-state diff is only visible in an incident request, so the suite
  cannot read it at an arbitrary time. **Decision:** on the first iteration
  each fault is *held* until 135 s after injection (past the 120 s settle)
  before any fix, and right after the incident closes it is **re-injected**.
  The second incident's diff must name the faulted object again. If the
  faulty state had been absorbed, the re-injected state would equal the
  baseline and the diff would be empty.
- Objects the wrong fixes or the recovery rolled out are allowed extras in the
  re-injected diff, because the baseline correctly still predates them.
- Before every other fault the suite waits until the controller has been quiet
  for 120 s + 20 s, so the baseline holds the current healthy state and the
  first diff must be **exact** (nothing but the faulted objects).

## N7. Diff liveness through `run.go` (coordinator-directed)

- `TestRunAttachesStateChangesMadeAfterStartupToIncident`
  (`controller/runtime/run_state_baseline_test.go`) runs `RunWithOptions` with
  a fake API client, the production ready-endpoints detector and a local
  responder that re-executes the test binary. The controller outlives its
  state-tracker start deadline before a Service selector changes; the incident
  must name the change. It fails against the pre-`13d5613` tracker and takes
  about 2 s, so it runs in CI.
- Two test seams were added to `RuntimeOptions`: `Client` and
  `StateBaselineStartTimeout` (default 30 s, as before).
- The live suite additionally asserts that its first fault comes after the
  controller has outlived the 30 s start deadline.

## N8. network_policy_block detection (coordinator-directed)

- The live e2e saw the traffic prober fire late (+20.4 s) in 1 of 4 runs on
  network_policy_block. The suite includes that fault in its latency
  assertions and reports the traffic prober's latency there as a known gap.
  The prober itself is not changed here (the coordinator dispatches that
  separately).

## N9. Harness bugs found while building the suite

- **SREGym recoveries read a fixed `/tmp` backup.** `missing_service`,
  `resource_request`, `sidecar_port_conflict` and `service_port_conflict`
  injected their backup at the per-cluster `fault_scratch_path` but recovered
  from `/tmp/<svc>_modified.yaml`, so on every named lane recovery applied a
  missing file. The isolation guard test only rejected `f"/tmp/` at the start
  of a string. Fixed test-first in SREGym `3018dbc2`.
