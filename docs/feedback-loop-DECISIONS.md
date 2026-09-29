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

- **Decision.** Each burst starts at a fresh iteration range: the prober
  advances its iteration counter by 2^32 per burst, and `Rand(iteration)` is
  PCG(seed, iteration). Consecutive bursts therefore do not repeat
  parameters. A burst restricted to an incident's failing scenarios also
  uses the workload seed XOR a hash of the sorted scenario IDs, and keeps the
  workload's total rate. Every failure names its seed and iteration, so it
  replays exactly.

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

### D15. `sdo incident status` and the verify-before-submit rule (item 2)

- **Decision.** `python3 -m sdo incident status [--workload] [--scenario]
  [--json]` asks the prober for one verify burst. It exits 0 when healthy, 1
  when unhealthy and 3 when the prober is unavailable. It lists the
  incident's `scenario-slo.*` scenarios first. The burst counts only
  *qualified* scenarios: ones the steady probe has seen pass at least once. A
  scenario that never passed cannot block, because it would also block every
  correct fix.
- **Alternatives.** Re-run the detectors from the responder pod, which would
  need controller internals and cluster-wide read access. Or trust the
  agent's own `curl` checks, which are too easy to satisfy with the wrong
  request.
- **Why.** The prober already has the workload, seeds and SLOs, and a burst
  takes about 3 s when healthy and about 4.6 s when unhealthy. The CLI stays
  in production code because it only speaks to the prober's HTTP API.

### D16. Prober address chosen at dispatch time

- **Decision.** `KubernetesJobDispatcher.DispatchEnvironment` adds
  `SDO_PROBER_URL` to each responder Job when it is created.
  `ProberEnvironment` checks the prober within 2 s and leaves the variable out
  if it cannot reach it, so the CLI then exits 3.
- **Alternatives.** Hard-code the Service DNS name in the responder image or
  the manifest.
- **Why.** Controller namespaces can be split, and the prober is optional. A
  prober address that does not resolve would turn every verify into a 2 s
  timeout.

### D17. Submission gate: exit 4 with no bypass

- **Decision.** SREGym's `submission.py` runs the live incident status
  before relaying a *mitigation* submission and refuses with exit code 4
  (`EXIT_VERIFICATION_FAILED`) while it is unhealthy. When the check is
  unavailable (exit 3), the submission goes through, and the receipt records
  that it was not verified. Diagnosis submissions are not gated, and there is
  no `--force` flag.
- **Alternatives.** A bypass flag, which agents would learn to use. Or
  blocking when the check is unavailable, which would turn a prober outage
  into a failed benchmark run.
- **Why.** A wrong fix, such as the Mongo-role re-grant decoy, is refused in
  about 4.6 s. The gate lives in the benchmark adapter, so `controller/runtime`
  stays transport-neutral.

### D18. State baseline: informers, a 2-minute settle, and digests (item 1)

- **Decision.** `StateTracker` runs namespace-scoped informers over Service,
  Deployment, StatefulSet, DaemonSet, NetworkPolicy, ConfigMap, Secret, Role
  and RoleBinding.
  - **Settle.** The first healthy evaluation sets the baseline. After that, a
    candidate snapshot taken at the start of a healthy stretch replaces the
    baseline only after 2 minutes with no unhealthy evaluation. A maintenance
    pause resets the baseline.
  - **Digests.** ConfigMap and Secret data, and env values whose names look
    secret, appear only as `sha256:` plus 12 hex characters.
  - **Exclusions.** SDO's own objects, `kube-root-ca.crt`, service-account
    token Secrets and Helm release Secrets are excluded.
  - **Limits.** Output is capped at 40 changes, 12 fields per change and 160
    characters per value.
  - **RBAC and failure mode.** The controller's Role gains read access to
    secrets, statefulsets, daemonsets, roles and rolebindings. A kind it
    cannot list is reported as `unobserved_kinds` and does not fail.
- **Alternatives.**
  - Listing everything at dispatch: slower, and it lands on the critical path.
  - A baseline taken once at install: stale after legitimate rollouts.
  - Showing ConfigMap bodies: would leak secrets and invite the agent to read
    decoy scripts as evidence.
- **Why.** In the smoke, startup took 167 ms and a diff took 1 ms, off the
  critical path. Decoys that existed at baseline can never appear. The settle
  period keeps a half-broken rollout from becoming the new "healthy" state.

### D19. Evidence contract (item 4)

- **Decision.** Each `ConfirmedRootCause` now carries:
  - `evidence`: at least one item, of kind `detector-finding`,
    `synthetic-traffic`, `state-change` or `live-observation`;
  - `explained_detectors`: at least one;
  - `static_context`: optional.

  The strict output schema has no static evidence kind. Scripts, manifests
  and ConfigMap bodies can only go in `static_context`, which is recorded but
  never counts as proof. Go's `ValidateFor` does not reject legacy results;
  the fields are `omitempty`.
- **Alternatives.** Free-text evidence, which cannot be checked. Or a
  `static` kind that verification rejects, which lets the model try it
  anyway.
- **Why.** Leaving the category out of the schema is cheaper than rejecting
  it after a turn has been spent. Old outcome records still load.

### D20. Deterministic diagnosis verification is recorded, not gating

- **Decision.** `verify_diagnosis` checks each cited detector, scenario and
  state change against the incident request and the detector states. Each
  root cause gets one verdict:
  - `confirmed`: every explained detector fired at dispatch and cleared after
    the fix, and nothing cited was contradicted;
  - `contradicted`: something cited was never observed;
  - `unverified`: nothing was contradicted, but not every detector flipped;
  - `no-evidence`: a legacy cause.

  The verdict goes into the outcome record and the SREGym receipt. It does
  not block incident closure.
- **Alternatives.** Refuse closure on `contradicted`.
- **Why.** Closure follows health, and a correct fix with a sloppy
  explanation should still close. The verdict matters for learning (D22) and
  for analysis.

### D21. Responder helper label and cleanup timing (item 5)

- **Decision.** Responders label each helper pod or Job they create with
  `sdo.dev/responder-helper=true`. When a responder completes successfully,
  the controller deletes the labelled Jobs and pods, with background
  propagation and a 10 s timeout, in the application namespace and the
  controller namespace. This happens before the closure gate is evaluated.
  The closure records `cleaned_helpers`. The flag
  `--clean-responder-helpers` defaults to true.
  - **RBAC.** In split-namespace mode, a narrow Role
    `sdo-controller-helper-cleanup` (list and delete on pods and jobs) is
    bound to the `sdo-controller` ServiceAccount. Shared mode already had
    these verbs.
- **Alternatives.**
  - Clean up at closure: a helper pod such as a curl loop or a stuck Job can
    itself keep a detector firing and block closure.
  - Garbage-collect by owner reference: responders do not own the objects.
- **Why.** A leftover helper is a red herring for the next incident and can
  mask the health of the current one.

### D22. Reflection learns only from confirmed causes (item 6)

- **Decision.** The reflection prompt now includes the deterministic
  verification. It tells reflection to write playbooks and incident detectors
  only from `confirmed` causes, to record contradicted or unverified
  explanations only as warnings, and to include a `## Verification` section
  that names the detectors which flipped.
- **Alternatives.** Skip reflection unless every cause is `confirmed`. That
  would lose the "this was a decoy" lesson.
- **Why.** It keeps red-herring explanations out of operational memory
  without adding a turn.

### D23. Process: the disk-full interruption

`/mnt/data` ran out of space in the middle of the task. Work continued in a
scratchpad clone. Committing from the clone was refused as out-of-place
publication, so I stopped and reported it. After space was freed, the
changes were applied to the worktree as a patch, tested, committed as
separate commits, and the clone was deleted.

### D24. Module-boundary edges for incident status

- **Decision.** `tach.toml` now allows `benchmarks.sregym.adapter` and
  `sdo.__main__` to depend on `sdo.agent_runtime.responder`. They use it only
  for `live_incident_status` and `run_incident_status_cli`.
- **Alternatives.**
  - Move the status client into `sdo.operational_memory`: it is not memory.
  - Route it through `sdo.operation`: that only adds a pass-through module.
- **Why.** The adapter is allowed to consume the production API, and the
  status client belongs to the responder that calls it. Both edges point from
  benchmark or entry-point code into production code, so no production module
  gains a benchmark dependency.

### D25. Prober dial hygiene and dial-failure classification

- **Problem.** In the `network_policy_block` live eval (see "Feedback-loop
  e2e results" below), the traffic detector fired late (+20.4 s) in 1 of 4
  runs and never fired in the other 3, even though the fault denies all
  ingress and egress to `recommendation`.
- **Investigation.** `controller/runtime/prober.NewHTTPClient` already
  builds its transport with `DisableKeepAlives: true` and a 2 s
  `net.Dialer` timeout (D5), so the prober's own requests to `frontend`
  already dial a fresh connection every time and already fail within
  seconds if `frontend` itself were blocked. The fixture's `recommend`
  scenario (`tests/fixtures/hotel_reservation/traffic/generators/generators.go`)
  targets `frontend`, not `recommendation` directly, because `frontend`
  proxies the call internally (matching the real hotel-reservation
  service). The fault only denies `recommendation`, which `frontend` is not
  probed against directly.
- **Confirmed.** The prober-to-frontend hop was never the masked
  connection. The mechanism is `frontend`'s own internal call to
  `recommendation`: a NetworkPolicy denies *new* connections but does not
  tear down ones already established (most CNIs allow already-tracked
  flows through), so `frontend`'s pre-existing connection or channel to
  `recommendation` keeps working, and `frontend` keeps returning 200 to the
  prober, until something makes `frontend` redial (idle timeout, an error,
  a restart). That explains sdo3's pattern: every scenario, including ones
  that never call `recommendation`, started timing out from +16 s,
  consistent with `frontend` itself degrading (for example thread or
  connection-pool exhaustion) once its call to `recommendation` started
  blocking, rather than a prober-side defect.
- **Decision (prober-side, in scope).**
  1. Keep `DisableKeepAlives: true` as the primary mechanism (already the
     strongest option: a per-request fresh dial, not merely a periodic
     one); no change needed here.
  2. Shorten and name the dial timeout explicitly:
     `prober.DialTimeout = 1 * time.Second`, clearly shorter than
     `traffic.DefaultTimeout` (2 s), so a blocked dial is always resolved,
     and classified, by its own timer well before the general per-request
     deadline would otherwise cancel it with a generic "context deadline
     exceeded".
  3. Add `traffic.Sample.DialFailed`, set from a new `isDialError` check
     (`errors.As` to `*net.OpError` with `Op == "dial"`) in
     `controller/sdk/traffic/engine.go`. It is orthogonal to `Outcome`: a
     blocked dial that times out is still `OutcomeTimeout` with
     `DialFailed = true`; an outright refusal is `OutcomeError` with
     `DialFailed = true`. `window.go`'s `StatusCounts` gets a `"dial"`
     bucket for the non-timeout case, and `detector.go`'s evidence text
     prefixes a failure's label with "dial" when `DialFailed`, so a health
     judge or responder reading a finding's evidence can tell a
     connection-level fault from an HTTP-level one without parsing the
     free-text error string.
  4. No workload YAML or generator changes; `.sdo/diagnostics/traffic/`
     ownership and the fixture's scenarios are unchanged.
- **Alternatives.**
  - A per-probe `http.Transport` instance instead of a shared one with
    `DisableKeepAlives`: no behavioral difference once keep-alives are
    off, since the shared transport already never pools a connection to
    reuse; would only add allocation overhead per iteration.
  - A periodic `CloseIdleConnections()` ticker: weaker than disabling
    keep-alives outright (a connection could still be reused between
    ticks), and redundant given D5 already disables them.
  - Subdividing "timeout" into "dial-timeout" vs "read-timeout" buckets in
    `StatusCounts`: more granular than the task's ask ("classify dial and
    timeout failures clearly"), and `Sample.DialFailed` already lets a
    consumer recover that distinction per-sample without adding another
    bucket key.
- **Residual gap (out of scope, not fixed here).** The frontend-to-
  recommendation path is application-internal: the prober never talks to
  `recommendation`, and cannot force `frontend`'s own client to redial.
  Closing this gap needs one of: a generator that calls `recommendation`
  directly (bypassing `frontend`, changing what the workload asserts about
  the real request path), or an app-level fix (the frontend's own HTTP or
  gRPC client would need short keep-alive/idle timeouts or health-aware
  connection management), or continuing to rely on the static
  `network-policy-total-isolation` health-judge rule and the state-change
  diff, which is what actually caught this fault at +0 s in all 3 SDO
  runs. This is a different fix than the prober's own hygiene and is
  recorded here, not implemented.
- **Expected effect.** Once a probe's dial to `frontend` is itself blocked
  (for example a NetworkPolicy that includes `frontend`, or a selector
  fault), the shorter, explicit `DialTimeout` and the `DialFailed`
  classification make that failure fire, and be legible as a
  connection-level fault, within about 1 s instead of racing the 2 s
  per-request timeout non-deterministically. For `network_policy_block` on
  `recommendation` specifically, no change in probe-side timing is
  expected, because the residual gap above is what masks it; the static
  rule and the state diff remain the primary detectors for this fault.

### Combined final smoke (no LLM, throwaway kind `sdo-smoke`, 3 runs)

The same setup as above, with the decoys mounted. One driver runs the
production `StateTracker`, the traffic and `service-endpoints` detectors, the
`python3 -m sdo incident status` CLI (run through `uv`, so its wall time
includes about 1 s of `uv` startup), and `KubernetesHelperCleaner`. The
prober image predates the `qualified` verdict field, which does not matter
here because every scenario qualifies on the healthy application.

| Phase | Result (runs 1, 2 and 3) |
|---|---|
| State tracker startup | 134, 135 and 134 ms; no unobserved kinds |
| Healthy with decoys | No findings in 66 evaluations over 20 s. The baseline diff has 0 changes. Verify burst healthy in 3.0 s. `incident status` exits 0 |
| Selector fault | First unhealthy verdict at 1.27, 1.90 and 1.50 s. `scenario-slo.search-hotels` fired at 1.57, 2.21 and 1.79 s, with `service-selector-matches-no-pods` on `Service/frontend` 0.3 s later. The state diff (about 1.1 ms) has exactly one change: `Service/frontend` selector `io.kompose.service=frontend` to `current_service_name=frontend,io.kompose.service=frontend`. No `failure-admin-*` object appears. Verify burst unhealthy in 4.6 s. `incident status` exits 1 |
| Decoy fix (SREGym's Mongo privilege-restore scripts, which reported "Privilege restored successfully") | Still firing for 20 s. The diff still points only at the selector. Verify burst unhealthy in 4.7 s. `incident status` exits 1, so the submission gate would refuse it with exit 4 |
| Correct fix | First healthy verdict at 1.90, 2.47 and 1.90 s. Cleared at 2.20, 2.78 and 2.21 s. Verify burst healthy in 3.0 s. `incident status` exits 0. 0 state changes remain |
| Helper cleanup | The labelled helper pod was deleted in 10–13 ms. An unlabelled bystander pod was kept |

**Wall-clock impact.**

- **Incident path.** The state diff is read from the informer cache, taking
  about 1 ms at incident open. Its informers start in about 135 ms, in
  parallel with prober startup, at controller launch.
- **Dispatch.** `SDO_PROBER_URL` adds at most a 2 s prober check, which
  normally returns in milliseconds.
- **Responder.** Each self-check costs 3.0 s when healthy and about 4.6 s
  when unhealthy. That replaces the ad-hoc `curl` checks responders already
  ran.
- **Closure.** Helper cleanup adds about 10 ms before the closure gate.
- **Verification.** Diagnosis verification is pure Python over data the
  broker already holds.

### D26. N11 fix: verification-time state diff, not a wider request or a settle window

- **Renumbered.** Logged as D25 on `vic/fix/late-fault-diff` (`341a7c5`).
  The RC1 integration (`vic/integrate/assurance-rc1`) renumbered it to D26
  because the prober dial fix's D25 (`276d574`) merged first. D27 (traffic
  min-duration) keeps its number.
- **Found.** Phase-1 qualification (`QUALIFICATION.md`, `NO_LLM_SUITE_DECISIONS.md`
  N11): on a composite fault the second component can land after the
  incident already opened (K2's readiness-probe fault about 6 s after its
  selector fault; K1 in 1 of 3 runs), so `IncidentRequest.state_changes`, an
  immutable dispatch-time snapshot, never names it. `verify_diagnosis`
  checked `state-change` evidence only against that snapshot, so a
  responder that correctly cited the late fault got `contradicted` or
  `unverified` instead of `confirmed`, and D22's reflection rule then
  learned nothing from it.
- **Decision.** Keep the request an immutable dispatch snapshot (per N11's
  original note: holding dispatch for a batching window slows every
  incident, not only composites). Instead, `Controller.maybeCloseIncident`
  now also computes `Baseline.Changes(now)` at verification time and carries
  it on `IncidentClosure.FinalStateChanges` (Go), threaded through
  `BrokerClosure`/`OutcomeFacts.final_state_changes` (Python) into
  `verify_diagnosis`, which checks `state-change` evidence against the union
  of `request.state_changes` and `final_state_changes`. A change absent from
  both is still `contradicted`.
- **Alternatives considered.**
  - **Refresh the diff into the request.** Would mutate a value already
    handed to the responder and cited in its own reasoning; the request
    stays a snapshot of what the responder saw, by design (same reasoning as
    the "no bypass" gate in D17 and the immutable-request choice already
    made in N11).
  - **Hold dispatch for a short settle window** so both components of a
    composite land before the snapshot is taken. Rejected: it adds
    wall-clock time to every single-fault incident too, and D18 already
    rejected a similar settle-before-dispatch tradeoff for the baseline
    itself for the same reason.
  - **Widen `verify_diagnosis` to accept any evidence unconditionally**
    (drop the state-change check). Rejected: it would stop rejecting decoys
    and never-happened changes, which is exactly what N10/N11's qualification
    run showed the check catching.
- **Why this design adds no wall-clock time.** `Baseline.Changes(now)` is the
  same in-memory informer-cache diff already computed for the live
  `incident_view` (N10) at every evaluation; computing it once more at
  closure, which already happens synchronously, costs no additional wait and
  keeps verification deterministic (no LLM, no benchmark-verdict inspection).
- **Tests (test-first).** `controller/runtime/controller_closure_state_diff_test.go`
  reproduces the K2 timeline (selector fault at dispatch, readiness fault
  landing before the health detectors clear) and asserts
  `IncidentClosure.FinalStateChanges` carries both while
  `IncidentClosure.Request.StateChanges` keeps only the first; a second test
  asserts a closure without a configured baseline never fabricates a diff.
  `tests/unit/sdo/operational_memory/test_diagnosis.py` and `test_outcomes.py`
  add the Python-side pair: a late-landing state change is `confirmed`
  against the closing view, and a decoy or never-happened change absent from
  both diffs is still `contradicted`.
- **Docs.** `.agents/skills/analyze-experiment/references/trajectory-schema.md`
  and `failure-patterns.md` now describe `final_state_changes` and how it
  changes what a `contradicted` state-change citation means on a composite.

### D27. Minimum-duration firing policy for traffic findings

- **Decision.** `sdk.PersistencePolicy` (`controller/sdk/detector.go`) gains
  `MinDuration time.Duration`: a finding must additionally have been observed
  continuously for at least this long before it activates, checked in
  `FindingStateTracker.Observe` (`controller/runtime/finding_state.go`)
  against a per-finding `FirstSeenAt` timestamp. `traffic.NewDetector`
  (`controller/sdk/traffic/detector.go`) defaults it to
  `DefaultHealthMinDuration` (9s) for any health-class spec that leaves it
  unset, so this reaches judge-authored, lifecycle-generated, and already
  seeded `.sdo/` traffic-health detectors alike with no template or seed edit.
  `TRAFFIC_AUTHORING` (`sdo/agent_runtime/lifecycle/agents.py`) now documents
  the policy so a health judge does not set it back down.
- **Why 9s.** Per N13
  (`benchmarks/sregym/experiments/assurance/NO_LLM_SUITE_DECISIONS.md`), the
  traffic-health detector fired on kind-worker data-plane stalls of about 3s
  (roughly 1 in 6-10 pod-network changes) because its window is
  re-evaluated on every 500ms probe poll, reaching the 2-evaluation firing
  threshold in about 0.5s; the same suite detects real faults in 3-5s. 9s
  clears the observed stalls with about 3x headroom while adding at most one
  more 500ms poll beyond real-fault detection, so a live evaluation no longer
  loses a responder session to a stray stall.
- **Alternatives.**
  - Raise `Firing` (evaluation count) instead of adding a duration: the
    window's re-evaluation rate is itself variable (poll cadence, prober
    scheduling), so a count threshold cannot target a specific wall-clock
    stall length the way a duration can.
  - Apply the default to every detector, not just health-class traffic ones:
    would silently delay responder-authored incident detectors, which the
    validator already requires to fire on their first match
    (`INCIDENT_DETECTOR_MAX_FIRING`).
  - Put the default in the Python lifecycle templates
    (`_traffic_detector_source`/`_GENERIC_SPEC_FIELDS`) instead of the Go SDK:
    would require rewriting already-seeded `.sdo/diagnostics/` detector.go
    files (marked "DO NOT EDIT" and independently validated) to take effect;
    the SDK default reaches them for free since they already call
    `traffic.NewDetector`.
- **Tests.** `controller/runtime/finding_state_test.go`
  (`TestFindingStateMinDurationSuppressesBriefStall`,
  `TestFindingStateMinDurationFiresSustainedFault`) simulate a 3s burst at
  the suite's 500ms poll cadence (never activates) and a sustained fault
  (activates within the 9s minimum plus one poll).
  `controller/sdk/traffic/detector_test.go` covers the default being applied,
  overridable, and scoped to health-class specs.
  `controller/core/validation_test.go` covers the new negative-duration
  rejection. `TestControllerOpensIncidentFromFailingSyntheticTraffic`
  (`controller/runtime/traffic_observer_test.go`) was extended to step past
  the new default so it still exercises incident assembly.
- **Pending.** Rerunning the no-LLM suite's `churn1` soak and a
  NetworkPolicy/selector case on a live kind cluster to measure the effect
  directly was in scope but skipped: no `assure-s0`/`assure-s1` cluster was
  reachable from this worktree, and cluster creation was out of scope.

### D28. The submit gate's clear hysteresis is separate from closure's

Branch `vic/perf/status-clear-latency`. This follows up on the cost recorded in
`benchmarks/sregym/experiments/assurance/NO_LLM_SUITE_DECISIONS.md` N10.

- **Where the ~30 s came from.** The source was clear persistence plus the
  detector's interval. Evaluation cost, the view's publish cadence and the
  prober window were not factors. A fix's own watch event triggers one clear
  evaluation, so `ClearCount` becomes 1 and the finding stays active.
  `ConfirmationInterval` (1 s) expedites only findings that are *pending
  firing*. The second clear evaluation therefore waits for the next watch
  event or the detector's interval: 30 s for `health-objective` and 15 s for
  `service-endpoints`. The view is republished after every step, so it was
  never stale; the finding really was still active. Readiness and
  missing-service faults cleared in 4 s because their rollout emits a stream
  of Pod events, which gives the second clear evaluation early.
- **Decision.** The submit gate gets its own hysteresis,
  `GateConfirmationPolicy`. Production uses 3 consecutive clear evaluations
  spanning at least 2 s, re-checked every 1 s.
  - **Re-evaluation.** While a finding that closure waits on is clearing, the
    controller re-evaluates its detector every second against a fresh
    informer snapshot, for the gate only. These gate evaluations never reach
    `FindingStateTracker`, the detector history or the state baseline.
    Closure, `final_detector_states` and the health judge's persistence
    policy are unchanged.
  - **Reset.** Any firing observation resets the finding's count, whether it
    comes from a scheduled, watch-driven or gate evaluation. A detector error
    also resets it.
  - **Publication.** The incident view moves gate-confirmed findings from
    `blocking_findings` to a new `clearing_findings` list.
    `sdo incident status` keeps refusing on any blocking non-traffic finding,
    and reports clearing findings as `ok`.
  - **Scope.** Gate state is not persisted, so a restarted controller
    confirms again from fresh evaluations. Re-evaluation stops once a finding
    is confirmed. A relapse still blocks at once, because the change that
    causes it emits a watch event.
- **Alternatives.**
  - *Expedite the clear confirmation for closure too*, by re-running a
    clearing detector after `ConfirmationInterval`. That changes the health
    judge's policy (N10), and closure runs after the mitigation submit, so it
    gains nothing for TTM. `incident_cost.py` defines `ttm_s` as the
    submission time minus the grading wait, floored at the last mitigation
    mutation, so closure time is already outside TTM.
  - *On-demand re-evaluation when status is queried.* The responder would
    need a request channel into the controller. Today it only reads a
    ConfigMap, and `controller/runtime` stays transport-neutral.
  - *Republish the view on informer changes.* It was already republished at
    every step, so this addresses nothing.
- **Guarantees kept** (tests are in
  `controller/runtime/gate_confirmation_test.go` and `test_incident_status.py`):
  - A wrong fix is still refused: the finding keeps firing, is never counted
    clear, and triggers no gate evaluations.
  - A partial composite fix is still refused: counts are kept per finding, so
    the unfixed finding keeps blocking while the fixed one moves to
    `clearing_findings`.
  - A finding that flaps on a 3 s cycle (clear for 2 s, then firing for 1 s)
    never passes over 60 s.
  - Watch-event bursts cannot beat the time window.
  - Closure still waits for the detector's own second clear evaluation.
  - The traffic side is still judged by the fresh verify burst.
- **Measured.** The runs used my own 1+1 kind cluster `assure-l0` (now
  deleted), with `--hold none` and 2 iterations per case. "Before" is
  `b1ba89c` (8 cases); "after" is this branch in two passes (16 cases).
  Fix-to-healthy status is `clear_s`; fix-to-closure is `verify_s`.

  | Case | Before `clear_s` | After `clear_s` | Before `verify_s` | After `verify_s` |
  |---|---|---|---|---|
  | selector-mismatch | 31.7, 31.7 | 4.7, 3.9, 4.1, 4.1 | 43.9, 43.4 | 23.5, 23.9, 23.8, 23.5 |
  | missing-configmap | 36.3, 31.7 | 8.3, 4.2, 4.1, 8.4 | 55.4, 44.3 | 25.8, 25.1, 23.9, 19.2 |
  | network-policy-block | 36.1, 31.9 | 4.1, 8.7, 8.7, 8.6 | 55.1, 44.6 | 14.3, 25.6, 25.5, 25.8 |
  | configmap-geo+selector | 31.7, 31.6 | 4.1, 4.1, 3.8, 4.8 | 43.7, 43.8 | 23.2, 23.9, 13.7, 23.7 |

  - **Gate checks.** Every wrong-fix and partial-fix probe was still refused
    by both status (exit 1) and the gate (exit 4). There were no stray
    incidents.
  - **The 8 s mode.** After the change the values fall in two groups, about
    4 s and about 8.5 s. The suite polls status, and each status call takes
    about 4.5 s: a burst, then a read of the view. When a call's read lands
    just before confirmation (about 2–3 s after the fix), the next call is
    the healthy one. A responder that checks immediately after its fix sees
    the same pattern, so it may need one retry.
  - **Closure got faster too.** The responder returns sooner, and closure's
    unchanged 2-evaluation rule is then met by the scheduled evaluation.
- **One unrelated check failure.** The diff check on after pass 1,
  missing-configmap #1, also named `Deployment/mongodb-geo`, the fault's own
  scale-down and scale-up. This is a baseline and diff timing effect under
  `--hold none` (N11 territory). The gate never reads the diff. It happened
  in 1 of 24 cases and was not reproduced in the other 23.

### D30. A strict receipt records how the incident closed; only a mitigation needs reflection

Branch `vic/fix/receipt-rejections`, from the phase-1 live run (`vic/exp/phase1-run`,
`benchmarks/sregym/experiments/assurance/PHASE1_RESULTS.md`, section 5). D29 is taken on another branch.

- **What failed.** 5 of 40 strict receipts were rejected, in two shapes.
  - *External recovery (B2, B9, C8).* `recovery_attribution = external`,
    `reflection_attempts = 0`, `reflection_commit = null`, `same_session_reflection = false`, and one recorded
    repair action. The broker never reflects on an `external_recovery` outcome (F8), so no reflection and no
    reflection commit exist, yet the receipt validator required `same_session_reflection=true` (mode `resume`), a
    reflection commit, and `validator_evidence_commit = reflection_commit`. It also rejected `external` outright
    (F17). The receipt could never pass.
  - *Cancelled no-op (C5, D9, S3 flicker stages).* The gate cleared before any repair, the responder recorded
    no action and reported `cancelled`, so `completed = false` and `repair_actions = []`. The validator required
    `completed=true` and a successful repair action.
  - In B9 the health-clear time (00:58:23) precedes the recorded repair's start (00:58:30), so attribution said
    `external` although the responder did change the cluster. This is an attribution-timing question (a responder
    that applies its fix before it records the action), not fixed here; the receipt is now valid and not counted
    as a mitigation either way.
- **Why one receipt killed a stage.** The receipt is validated when the *next* stage drains it, before that
  stage injects. `drain_pending_incident` wrote the rejected file and raised, leaving `record.pending` set. The
  next stage's install raised before injection (C stage 9), and teardown drained the same incident again, failed
  again, and returned an error (B and C teardown exit 1).
- **Decision (contract).** A receipt is an audit record of a verified closure, and credit is a separate field.
  `receipt_resolution` derives one of three values from the receipt's own evidence, and the builder writes it as
  `resolution`; an explicit value that disagrees with the evidence is rejected.
  - `sdo_mitigated`: a successful repair action or a proposal commit, attribution not `external`. Unchanged
    contract: `completed=true`, same-session reflection per `reflection_session_mode`, a reflection commit,
    `validator_evidence_commit = reflection_commit`.
  - `external_recovery`: the responder repaired something but attribution is `external`. Valid; reflection is
    optional (none is recorded), `completed` may be either value, and `validator_evidence_commit` equals the
    reflection commit if there is one, else the outcome commit.
  - `cleared_without_sdo_action`: no successful action and no proposal commit. Same rules as `external_recovery`.
  - Every kind still requires the integrity evidence: dispatch, acknowledgement, cleanup, empty remaining
    worktrees, passing independent verification, clear detectors with no late detector review, lifecycle
    provenance, valid actions and commits. Only `sdo_mitigated` is counted as SDO's fix. A responder that recorded
    a successful repair but reports `cancelled` is still `sdo_mitigated` and still rejected for `completed=true`.
- **Decision (outcomes).** New classification `cleared_without_action` for a verified-clear closure where the
  responder made no successful change and no repository commit, whether it reported `completed` (previously
  `cancelled`, F16) or `cancelled`. `cancelled` now means only a cancelled responder that also failed to restore
  health, or one that did change something. The broker never reflects on it, and the controller only surfaces
  `success` outcomes as prior evidence, so no Go change is needed. `external_recovery` is unchanged.
- **Decision (run records).** Fastloop `IncidentRecord.sdo_resolution` carries the validated receipt's
  resolution (null when no receipt validated); the no-LLM suite's `mitigated` check reads the resolution, not
  `completed`, and expects `cleared_without_action` for the healed-stray scenario.
- **Decision (blast radius).** A rejected receipt is now that incident's outcome only. `drain_pending_incident`
  still writes `sdo_rejected_production_receipt.json` and raises `ReceiptRejectedError`, but the pipeline
  callers (`run_persistent_stage`, `teardown`) record a warning, clear `pending` and go on: the next stage
  injects and teardown returns no error. After teardown the runner marks a stage whose strict receipt is
  missing `agent_failure` (not `failed`) under `continue_on_agent_failure`, only when the teardown itself had no
  error; otherwise the pipeline still fails as before. Fastloop's `learn` still reports the rejection as the
  record's `error`.
- **Alternatives.**
  - *Make the reflection optional for every receipt.* Rejected: a mitigation's learning is what the strict receipt
    proves; only the credited case keeps that requirement.
  - *Keep rejecting external receipts and only fix the stage kill.* Rejected: it discards valid evidence, and the
    phase-1 reports counted the five as product failures.
  - *A separate receipt filename for non-mitigated closures.* Rejected: every consumer (run validity, cost
    analysis, pipeline gate) would need to learn a second file for the same incident.
- **Tests.** `tests/unit/benchmarks/sregym/protocol/test_receipt_resolution.py` validates the five real
  rejected receipts (fixtures in `tests/fixtures/sregym/phase1_rejected_receipts/`) and pins the mitigation
  contract; `test_persistent.py` covers the next-stage injection, teardown and single-drain paths;
  `test_pipeline.py` covers the per-stage `agent_failure`; `test_outcomes.py` and `test_broker_service.py` cover
  `cleared_without_action`; `test_fastloop_*` cover the run record.
- **Not done.** No live run. B9's attribution timing (above) and whether the responder should record actions at
  the time it applies them are open.

