# Robust incident feedback loop: decisions

Branch `vic/feat/robust-feedback-loop`. Each entry records the decision, the
alternatives considered, and why. Entries marked **user-directed** restate a
direction from the user; the rest were made autonomously.

## Verified facts (before design)

- **Decoys.** `third_party/sregym/sregym/service/apps/hotel_reservation.py`
  `deploy()` creates `failure-admin-geo` and `failure-admin-rate` with the
  revoke/remove-admin Mongo scripts. Its docstring says they are not mounted,
  but with the default `mount_failure_scripts=True` it also JSON-patches
  `mongodb-geo` and `mongodb-rate` to mount them at `/scripts`. Either way they
  exist before the fault is injected, so a diff against the pre-fault healthy
  state never contains them.
- **Closure gate.** `controller/runtime/controller.go` `maybeCloseIncident`
  closes only after the responder finished and every health detector's latest
  evaluation since responder completion is clear; `ClearThreshold` (2 in
  production) is applied per finding by `FindingStateTracker`.
- **Responder sandbox.** `sdo/agent_runtime/responder/codex.py` runs the
  responder with `access="danger-full-access"`, which `libs/agent_cli/structured.py`
  maps to `CodexSandboxConfig(mode="danger-full-access")` with no
  `excluded_commands`. The `sdo detector check` exemption exists only for
  `workspace-write` turns (deployer/health judge authoring). A new responder
  command therefore needs no sandbox exemption, and `structured.py` (being
  edited by other agents) does not need to change.
- **Submission before closure.** The benchmark adapter tells the responder to
  run `python3 -m benchmarks.sregym.adapter.submission mitigation ...` after
  "your own verification" (`benchmarks/sregym/adapter/runtime.py`
  `_responder_instructions`); nothing ties that to live detector state.
- **Baseline gate.** `benchmarks/sregym/adapter/fault_gate.py` injects the
  fault only after the controller logs an evaluation with no active finding,
  so the controller itself observes a healthy state right before injection.

## Decisions

### D1. Synthetic-traffic health detectors are the top priority (user-directed)

- **Decision.** Item 3 (generic symptom health detectors) moves first and is
  reshaped around end-to-end synthetic traffic: a source-grounded,
  health-judge-owned endpoint mix, low continuous rate, per-route sliding
  window SLOs with firing/clearing persistence, deterministic Go on
  `controller/sdk`. The same signal is the trigger and the acceptance test
  for `sdo incident status` (item 2). Structural checks stay as cheap
  complements.
- **Why.** User direction. Structural detectors cannot tell a correct fix from
  a decoy "fix"; user-visible requests can.

### D2. Source layout (user-directed)

- **Decision.** Generic machinery in `controller/sdk/traffic`; execution in
  `controller/runtime` (or a `controller/cmd/prober` binary); app knowledge
  only in `.sdo/diagnostics/traffic/` and health detectors in the manifest;
  schema in `models.py`, checks in `validation.py`, authoring in the judge
  prompt; hotel routes only in test fixtures.
- **Deviation.** Ownership of `.sdo/diagnostics/traffic/` is enforced in
  `MemoryValidator._actor_owns`, which the commit broker calls, rather than in
  `commit_broker.py` itself. That is where every other path ownership rule
  lives, so the broker enforces it without a second table.

### D3. Ground testing in one app and one decoy problem (user-directed)

- **Decision.** Test on hotel-reservation with
  `wrong_service_selector_hotel_reservation` and the `failure-admin-*` decoys
  only; drop the other three sequence faults this round. Unit tests cover this
  case plus generic no-false-positive cases; the code stays generic.
- **Evaluation plan.** First-encounter pass rate on this problem, SDO vs
  Codex, n>=3 each, in the fast loop after the quota decision.

### D4. First cut: in-process prober in the controller runtime (superseded by D9)

- **Decision.** The first implementation probes from a goroutine pool inside
  the controller runtime and exposes per-route sample windows to detectors via
  the detection context; detectors never make requests in `Detect`.
- **Alternatives.** HTTP inside `Detect` (blocks the loop, not deterministic
  over replays); a separate prober pod (extra pod in the app's view, more
  RBAC).
- **Why.** No blocking of the loop, no extra pod to perturb the "all pods
  running" oracle, and a notify path for fast detection. Superseded by the
  user-approved isolation requirement in D9; the SDK windows, SLO evaluation
  and detector are reused unchanged.

### D5. Probe hygiene

- Keep-alives disabled so a changed Service selector is visible on every
  probe rather than hidden behind a live connection.
- A route is judged only after it has succeeded once (qualification), so a
  wrong route in the mix can never fire; a 5 s warm-up precedes the first
  all-clear evaluation so the fault gate's baseline already contains samples.
- Windows reset on maintenance pause/resume.
- Probe results enqueue an evaluation only while a route has failures in its
  window, so a healthy app costs no extra evaluations.

### D6. SLO defaults

- Window 5 samples, min 3, max age 30 s, error rate >= 50 %, timeout rate
  >= 50 %, p90 <= 1.5 s, 4 req/s per mix (hard max 20), 2 s timeout.
- **Why.** At 4 req/s over 4 routes each route gets ~1 sample/s, so 3 failed
  samples fire in ~3–4 s plus firing persistence, and clearing takes ~4–6 s,
  inside the 5–10 s target, while one blip (1/5) never fires.

### D7. Per-port TCP reachability deferred

- **Why.** Apps with same-namespace-only NetworkPolicies would make a
  controller-namespace reachability check fire falsely. End-to-end routes plus
  the ready-endpoints check already cover the selector fault. The
  `servicehealth` ready-endpoints detector is the always-on zero-knowledge floor.

### D8. Mixes are optional and validated when present

- Apps that serve no HTTP get only the endpoint detector. Python and Go
  validators mirror each other; a parity test loads the shared hotel fixture
  in both. Generic registrations are appended as marked manifest blocks so a
  refresh replaces exactly them and never touches responder-owned entries.
- No sandbox exemption is needed for new responder commands: the responder
  runs `danger-full-access`.

### D9. Generators in Go, workloads in YAML, isolated prober (user-approved)

- **Decision.** Replace the YAML route mix with:
  1. Go endpoint/scenario generators behind an SDK interface in
     `controller/sdk/traffic` (`Build(ctx, rng, state) -> Request`,
     `Check(resp) -> Verdict`, chained with shared state), declaring the
     services they depend on (for localization), a side-effect class (read,
     idempotent write, write-with-cleanup) and an optional fault class they
     must detect. Helpers make a simple generated GET about one line. They
     live in `.sdo/diagnostics/traffic/generators/` and are authored by the
     health judge.
  2. The engine owns scheduling, arrival, rate caps, timeouts, latency
     measurement, SLO windows and persistence; randomness and time come only
     from a seeded rng and the engine clock so failing probes replay exactly.
  3. Workload profiles stay YAML in
     `.sdo/diagnostics/traffic/workloads/<name>.yaml` with purpose
     `health-probe`, `verify-burst` or `journey`.
  4. Generators run in a separate prober process/pod with egress only to the
     app namespace, no API credentials, resource limits and crash isolation;
     the controller reads results; `controller/runtime` stays transport-neutral.
  5. Validation reuses the detector toolchain; the lifecycle runs the live
     healthy/decoy/fault checks.
  6. Health judge owns generators and health workloads; responders may add
     incident-scoped ones after verified outcomes.
  7. The `servicehealth` endpoint floor stays.
- **Why.** User-approved design. Go generators express multi-step flows and
  generated parameters that YAML cannot, while the engine keeps all timing and
  load under deterministic control; process isolation keeps app-authored code
  away from the controller's credentials.

### D10. Prober placement and build

- **Decision.** The builder generates a per-application `cmd/prober/main.go`
  next to the detectors. It is compiled with `CGO_ENABLED=0` and published to
  the repository PVC at `.sdo-prober/<sha16>/sdo-prober`. The controller
  starts it as `sdo-prober` in the control namespace, reusing the controller
  image and mounting the binary directory read-only through a PVC subPath.
  The pod gets no service-account token, limits of 250m CPU and 128Mi memory,
  a read-only root filesystem, and dropped capabilities. A NetworkPolicy
  allows egress only to the application namespace and kube-dns:53, and
  ingress only from pods in the same namespace (the controller). The pod is
  reused when its fingerprint label matches.
- **Alternatives.** A static `controller/cmd/prober` baked into a new image.
  A sidecar in the controller pod.
- **Why.** The generators are app-specific Go code, so the binary must be
  built per application, as detectors already are. Reusing the controller
  image means no new image and no change to `scripts/build_sdo_images.sh`
  (checked: no new Python dependencies and no new image). A sidecar would
  share the controller's network identity and credentials.

### D11. Static import and call guard for generators

- **Decision.** The builder rejects generator imports outside an allowlist:
  the SDK traffic package plus pure stdlib (`bytes`, `context`, `encoding/*`,
  `errors`, `fmt`, `math`, `math/rand/v2`, `net/url`, `regexp`, `sort`,
  `strconv`, `strings`, `time`, and similar). It also rejects `rand.X` calls
  other than the `Rand` type, and `time.Now`, `Since`, `Until`, `Sleep`,
  `After`, `Tick`, and the timer and ticker constructors.
- **Why.** Exact replay needs every source of randomness and time to come from
  the engine. It is defence in depth on top of the pod isolation, and it is
  cheap to check.

### D12. Fixture grounding, qualification, and write safety

- **Decision.** The hotel fixtures are grounded in the SREGym source:
  - `Cornell_<i>` users with the password made of the digit repeated ten times;
  - `/hotels`, `/recommendations` and `/user` routes, with bodies checked for
    `FeatureCollection` or `Login successfully!`.
  The `reserve` write (idempotent: marker customer `sdo-synthetic`, zero rooms,
  2099 dates) is used only in the journey workload, never in the steady
  health probe. At startup the prober must see each scenario pass once before
  its SLO can fire, and the controller logs `synthetic_traffic_warm` and the
  startup time.
- **Why.** An earlier fixture guessed hex user names. Qualification stopped it
  from ever firing, instead of producing a permanent false positive. This is
  the safety property we want when a judge authors a wrong generator.

### D13. Live lifecycle validation deferred to startup qualification

- **Decision.** The health-judge lifecycle compiles the generators, runs the
  conformance checks (each declared fault class must be detected against a
  simulated doer), and validates the workloads against the catalog. It does
  not yet run the "passes against the live healthy app" step. Startup
  qualification (D12) covers that at runtime and is logged.
- **Why.** Running the live check in the lifecycle needs the prober pod
  before the controller exists. That would add wall-clock time to deployment,
  and it cannot be exercised without a live agent run, which is out of scope
  here.

### D14. Verify-burst seeding

- **Decision.** A full burst uses the workload's seed. A burst restricted to
  an incident's failing scenarios uses that seed XOR a hash of the sorted
  scenario IDs. Both replay exactly from the seed and iteration in the
  evidence, and the restricted burst keeps the workload's total rate.
- **Why.** Deterministic replay matters more than parameter variety across
  repeated bursts. The steady probe already covers variety.

### Smoke results (no LLM, throwaway kind `sdo-smoke`, 3 runs)

Setup: hotel-reservation with SREGym's `failure-admin-geo` and
`failure-admin-rate` ConfigMaps mounted at `/scripts`, and the prober pod in
`hotel-reservation-sdo` running the fixture generators.

| Phase | Result |
|---|---|
| Healthy with decoys | No findings in 66–67 evaluations over 20 s; probe rate 4.04 req/s; verify-burst healthy in about 3.0 s |
| `wrong_service_selector` on frontend | First unhealthy verdict at 1.57, 1.59 and 1.92 s. Fired (2 evaluations) at 1.88, 1.89 and 2.21 s as `scenario-slo.search-hotels`, with evidence "connection refused", the request path frontend → search → geo → rate → profile, and the replay seed and iteration. `service-endpoints` pointed at the frontend selector label `current_service_name=frontend`. Verify-burst unhealthy in about 4.6 s |
| Decoy fix (re-grant Mongo roles) | Still firing for 20 s; verify-burst unhealthy in 4.5–4.6 s |
| Correct fix | First healthy verdict at 2.18, 2.52 and 2.47 s; cleared at 2.50, 2.83 and 2.77 s; verify-burst healthy in 3.0 s |
| Prober cost | About 11 millicores of CPU and 13 MiB of memory |
