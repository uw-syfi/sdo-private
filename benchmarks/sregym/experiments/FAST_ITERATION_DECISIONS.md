# Fast SDO experiment iteration: decisions log

Goal: cut the SREGym SDO development loop from about 20 minutes per two-round pipeline (under 3 minutes of which is the agent working on the incident) to a few minutes per incident. Owner: autonomous agent. Every entry states what was chosen, the alternatives, and why.

Reference run: `third_party/sregym/logs/20260927_174023_pipeline_sdo-codex-luna-persistent` (2 rounds of `missing_configmap_hotel_reservation`, persistent controller, kind `luna-w0`).

## Working environment

- **Separate clone, not a worktree of the main checkout.** This agent was sandboxed to a worktree of the SREGym submodule, and `git worktree add` against the main checkout would write into its `.git`. The parent work is in a clone at `/mnt/data/shli/sdo-fastloop`, on branch `vic/feat/fastloop`, based on `main` at `8c83a04`. Its `third_party/sregym` is a clone of the shared submodule repository. The submodule branch `vic/feat/fastloop-harness` is based on `38cbf4c7`, the commit `main` pins, not the worktree's default `180091bc`. When finished, the branch is fetched back into the shared submodule repository so it can be integrated.
- **Images are re-tagged, not rebuilt, for item 1.** `sdo-controller`, `sdo-responder`, `sdo-sregym-responder` and `sdo-detector-validator` got an extra `:fastloop` tag on the image IDs that `:v0.1.0` pointed to at 18:55 (`8173e51e7c3a`, `0c0946f1c335`, `efeea534d8c2`, `bb1ff6241ceb`). The shared tags are untouched.
  - Alternative: rebuild from the clone. Rejected: nothing in the fast loop changes image contents, and a rebuild costs CPU that would disturb the concurrent timed runs.
  - Item 4 changes the controller and validator Dockerfiles. Only those two were rebuilt from this branch and tagged `:fastloop`; the `v0.1.0` tags were never touched.
- **Own cluster `fastloop-w0`**, created through SREGym's own `create_worker_cluster` with prefix `fastloop-w`. It uses the same kind config, Calico enforcement, and `worker_cpu_limit = 3` as the `luna-*` runs, so incident timings are comparable.
- **Private buildx builder `fastloop` (docker-container driver).** Building the hotel image with the default Docker builder failed with `parent snapshot ... does not exist`. That builder's snapshot store is shared with the timed runs, so it was not pruned or repaired. A private builder never touches the timed runs' builder state or cache. `fastloop up` sets `SREGYM_DOCKER_BUILDER=fastloop` by default.

## Item 1: fast inner-loop harness

- **Split across the process boundary.** SREGym has its own virtual environment, and first-party code never imports it; the runner drives it as a subprocess. The harness keeps that shape:
  - `third_party/sregym/sregym/fastloop/worker.py` (fork) is a JSON-lines worker. It reuses the problem classes, fault injectors and oracles, plus `Conductor.deploy_app` for the one-time deployment. It never uses the conductor's stage machine, HTTP API, LLM judge, undeploy or reconciliation.
  - `benchmarks/sregym/fastloop/` (parent) drives the worker, the SDO persistent controller, or a raw Codex baseline.
  - Alternative: import SREGym into the parent's environment. Rejected: it would mix two dependency sets, and the architecture rule keeps SREGym behind a process boundary.
- **The worker keeps the problem instance of the incident in flight.** Some injectors keep state between `inject_fault` and `recover_fault`. A fresh instance is built for each incident, as SREGym builds one per problem run.
- **Private `/tmp` for the worker (`bwrap --bind <run>/tmp /tmp`).** SREGym injectors back up resources to fixed paths such as `/tmp/mongodb-geo_modified.yaml`. The concurrent `luna-*` runs use the same paths, so an injection here could overwrite their recovery input. Checked on this host: unprivileged user namespaces work, and docker and kind run inside the sandbox.
  - Alternative: make the injectors' state directory configurable. Rejected for now: about 30 call sites across 7 files, for a problem the sandbox solves without touching them. `--no-sandbox` opts out.
- **Shared-infrastructure side effects avoided.** The worker writes the baseline state to a per-cluster file (not the host-wide `~/cache_dir/cluster_baseline_state.json`), and stubs the MCP server deploy. That deploy port-forwards the fixed host port 9954 and kills the port's previous owner, which could be another run's forward. Loki is not deployed (`deploy_loki=False`); neither agent in this loop uses it.
- **The mitigation oracle is built per incident, after the app is up.** `MitigationOracle.__init__` snapshots deployment replica counts. In the conductor the problem is built before the namespace exists, so the snapshot is usually empty and the deployment checks are skipped. Here the snapshot is taken while the app is healthy, so the check is slightly stricter than the benchmark's. That is acceptable for a development signal.
- **Workspace seed.** The smoke test starts from the lifecycle-only seed `873a9a9` (the `64b3ac2` lifecycle plus its validation attestation). This is the same memory the timed experiments seed from, and has no incident playbooks.
  - Rejected: the reference run's stage-1 workspace, which already has learned memory. Its `verify.sh` runs `kubectl exec`, which the responder cannot do since the RBAC rule (`24a01b8`). Warm incidents from it would measure a broken playbook.
- **SDO runs without a benchmark submission transport.** The fast loop grades the cluster itself, so `RuntimeConfig.submission_api_base` stays unset and the responder's prompt has no submission instructions. Receipt validation already accepts that. `SdoAgentSettings` rejects a submission transport, so a fast-loop number can never include one.
- **Detection time is derived.** Controller evidence has no `detected_at` field. The loop uses `verified_at - incident_resolution_seconds`, the adapter's own definition of "detected to verified health". Detection can come slightly before `injection_finished_at`, because the injector's pod restart is part of the injection window.
- **Mitigation time is the latest successful repair's `completed_at`.** Failed repair attempts are excluded.
- **Reflection is measured separately.** `learn()` drains reflection and detector rollout after the oracle and fault recovery, and records `reflection_seconds`. Waiting for a previous incident's reflection inside the next `resolve()` is recorded as `previous_reflection_drain_seconds`.
- **The first incident pays setup.** Lifecycle reuse and controller install are recorded as `setup_seconds` and excluded from the resolution times, but they are part of `incident_wall_seconds`.
- **Codex baseline on the host CLI.** It uses the same `codex exec` flags, instruction (`clients/codex/driver.build_instruction`), filtered Kubernetes API proxy and `auth.json`-only `CODEX_HOME` as SREGym's Codex agent. The only difference is that it runs on the host (`codex-cli 0.157.1`) rather than in `sregym-agent-base`.
  - Alternative: the container. Rejected: it needs the conductor's network and harness wiring; the host CLI gives the same agent loop.
  - The proxy also hides `<namespace>-sdo`, so the baseline cannot read SDO's controller state or learned memory from the cluster.
- **Codex submissions go to a local stub.** The stub serves `/status`, `/get_app` and `/submit` with the benchmark's stage order, records each submission with its timestamp, and grades nothing. `mitigation_applied_at` for Codex is the time of its mitigation submission.
- **Lifecycle validator image.** Host-side lifecycle reuse uses `ContainerSandboxRunner`'s default `sdo-detector-validator:v0.1.0`, as the SREGym adapter does. The in-cluster controller uses the `:fastloop` validator. The fast loop does not change which validator the lifecycle trusts.
- **The first live SDO incident lost its timings to a stale module.** `sdo_agent.py` was reformatted while the process was already running, and the loaded copy lacked an import. The oracle still passed, and the next run drained its reflection, which accounts for the 235 s `previous_reflection_drain_seconds` of warm incident 0.
- **Fresh `CODEX_HOME` per baseline incident.** Codex CLI 0.157 keeps `memories_1.sqlite` and other state in `CODEX_HOME`. One home shared across a run would let the "memoryless" baseline learn between incidents, so each incident gets a new home holding only a copy of `auth.json`.

## Item 2: `POST /cleanup` timeout

- **Root cause.** `/cleanup` runs `_finish_problem` synchronously: fault recovery about 8 s, namespace deletion about 40 s, PV and job cleanup about 6 s, baseline reconciliation about 16 s, about 70 s in all. `signal_cleanup` gave up after 60 s. `main.py` then called `_finish_problem` again, which blocked on the same lock until teardown finished. The timeout never added time; it only logged a spurious failure. The real cost is the teardown.
- **Client fix, not an async endpoint.** `signal_cleanup` now waits up to 900 s, above the conductor's 600 s watchdog.
  - Alternative: return 202 and tear down in the background. Rejected: `main.py` waits for `done` before the next problem either way, so it saves no wall time, and it would change the endpoint's contract for every agent.
- **Opt-in fast namespace teardown (`fast_namespace_teardown`, fork `SREGYM_FAST_NAMESPACE_TEARDOWN`).** After the namespace is marked terminating, its pods are deleted with a zero grace period. Controllers stop recreating pods in a terminating namespace, so this only skips the pods' termination grace. PVs, jobs, fault recovery and reconciliation are unchanged. If the deletion fails, cleanup falls back to the normal wait.

## Item 3: deferred diagnosis grading

- **Opt-in conductor flag (`defer_diagnosis_grading`, fork `SREGYM_DEFER_DIAGNOSIS_GRADING`).** A diagnosis submission is recorded with its timestamp as before. The mitigation stage opens at once, and the same `_evaluate_diagnosis` runs on a background thread. `_finish_problem` joins it before teardown (bounded by `SREGYM_DEFERRED_GRADING_TIMEOUT_SECONDS`, default 1800 s), and `start_problem` joins it too. Results therefore carry the same `Diagnosis` and `Mitigation` verdicts from the same judge, plus `diagnosis_grading_deferred: true`.
- **TTL is measured at submission when deferred.** Otherwise it would include judge latency that the agent no longer waits for. `TTM` excludes diagnosis judging when deferred, and the analysis reference says to compare `TTM` only within the same mode.
- **The mitigation oracle can run while the judge is still grading.** The judge is an LLM call over the submission text and does not read the cluster, so the two do not interfere.
- **Only deferred when a later stage exists.** For a diagnosis-only task list, the flag keeps the old path, so the HTTP handler never runs teardown.

## Item 4: lifecycle snapshot cache and the baseline gate

- **Key and scope.** Stage 0's 210 s is `reuse_initial_lifecycle_if_valid` running the detector validator, because the seed's attestation names an older validator image. The workspace attestation already trusts exactly the pair (validator image identity, `.sdo/diagnostics` digest), and the validator reads nothing else (`check_cli test` loads `.sdo/diagnostics/manifest.yaml` and its packages). The cache therefore stores that verdict under that key, outside the workspace.
  - Alternative: key on seed commit, app source digest and all image digests. Rejected: those inputs do not change the validator's verdict. Keying on them would only lower the hit rate without adding safety. Source and topology checks still run on every reuse, before validation.
- **Only passing validations by an identified validator are stored**, atomically (temp file plus rename), so concurrent pipelines can share one directory.
- **Analysis can tell cached from fresh.** Receipts and resolutions gain `lifecycle_validation: {cache, source}`. On a hit, the workspace gets its own attestation commit (`sdo: attest lifecycle memory from the shared validation cache`, `attested_by: validation-cache`).
- **Runner opt-in `lifecycle_validation_cache = true`** points `SDO_LIFECYCLE_VALIDATION_CACHE_DIR` at `.sdo-runtime/lifecycle-validation-cache` in the checkout. The fast loop uses `<run-dir>/validation-cache` by default; `--no-validation-cache` turns it off.
- **Baseline gate (107 s): cold Go compile in the controller pod, not a watch window.** The controller builds the detector binary at start with `GOCACHE` on a fresh PVC, and the runtime image had no warm cache. Measured in the controller image at the pod's 2-CPU limit: 114 s cold, 6.5 s with a warm cache. The later window (two 30 s detector intervals at `firing: 2`) is not the cost, so shortening it was not needed. The reused-controller gate (1.1 s) confirms this.
- **The validator image's existing seed was mostly ineffective.** It was compiled in the `golang` stage with cgo on. The runtime images have no C compiler, so Go builds there with `CGO_ENABLED=0`. Cache keys include that setting, so nearly every entry missed: 88 s with that seed versus 6.5 s warm. Both seeds are now built with `CGO_ENABLED=0`.
- **The controller image ships a seed; the pod sets `SDO_GO_CACHE_SEED`.** Only the `controller` target copies the cache (about +1 GB), and responder images are unchanged. `go_runner` still fails if the seed is missing: image and manifest ship together, and version skew should fail loudly rather than silently cost minutes.
  - Alternative: point `GOCACHE` at a warm cache on the PVC. Rejected: the PVC is per install, and a cache on it outlives the image that produced it.

## Item 5: app rebuild

- **Opt-in build-context digest (`source_build_cache`, fork `SREGYM_SOURCE_BUILD_CACHE`).** A SHA-256 over paths, exec bits and contents of the Docker build context, excluding `.git`, `.sdo` and `.sdo-runtime` at its root. The image is labelled `sregym.source-digest=<digest>`; when the local image's label matches, the build is skipped and the image is only `kind load`ed (a no-op when the nodes already have it).
- **Why excluding `.git` and `.sdo` is safe.** The Hotel Reservation Dockerfile runs `COPY .`, so SDO's memory commits invalidate Docker's layer cache and trigger `go get` and `go mod vendor` from the network on every pipeline. The binaries do not depend on those directories.
- **Applied to both source-deploy adapters** (Hotel Reservation and Social Network) through one helper.

## Measurements (cluster `fastloop-w0`, `worker_cpu_limit = 3`, `gpt-6-luna`, `missing_configmap_hotel_reservation`)

All incidents passed the deterministic mitigation oracle.

| Run | Incident | Wall (s) | Injection to mitigation (s) | Injection to verified (s) | Gate (s) | Setup (s) | Reflection (s) | Responder tokens |
|---|---|---|---|---|---|---|---|---|
| `sdo-warm-3` | 0 | 300.1 | 14.3 | 48.6 | 1.1 | 5.6 | 4.9 | 145,581 |
| `sdo-warm-3` | 1 | 72.8 | 25.2 | 60.6 | 1.1 | - | 6.5 | 162,527 |
| `sdo-warm-3` | 2 | 73.6 | 26.2 | 55.8 | 1.2 | - | 11.9 | 156,997 |
| `codex-1` (baseline) | 0 | 85.1 | 79.3 | 82.7 (Codex exit) | - | - | - | 298,928 |
| `sdo-seeded-2` (fresh install) | 0 | 309.6 | 38.2 | 98.6 | 4.4 | 185.1 | 4.0 | 229,808 |
| `sdo-seeded-2` | 1 | 70.4 | 34.7 | 57.3 | 1.2 | - | 7.3 | 157,245 |
| `sdo-cachehit-1` (fresh install) | 0 | 93.4 | 24.0 | 56.0 | 4.6 | 18.3 | 3.5 | 155,153 |

- All SDO incidents took the warm path (exact-fingerprint match) with zero reflection tokens: "repeated exact-match success".
- `sdo-warm-3` #0 includes 235 s draining the reflection of the interrupted cold incident `sdo-cold-1`. That cold incident took 405 s in all: 148 s lifecycle revalidation, 11 s install, 86 s cold baseline gate, 150 s injection to verified recovery.
- A steady-state warm incident takes about 70–75 s end to end, against about 20 minutes per two-round pipeline before.
- `sdo-seeded-2` #0 used the rebuilt controller image (item 4): gate 85.6 s -> 4.4 s. Its lifecycle validation ran the `v0.1.0` validator (`source: validator`, 172 s) and stored the verdict.
- `sdo-cachehit-1` reinstalled from the pre-attestation state of the same memory, as a new pipeline would: `source: validation-cache`, lifecycle 4.4 s (was 148–172 s), fresh install plus incident in 93 s.
- Go build probes (item 4), `check_cli test` on this workspace:
  - controller image at 2 CPUs: 113.7 s cold, 6.5 s warm, 88.3 s with the old cgo-on seed, 7.2 s with the new image's seed;
  - validator at 1 CPU: 152.1 s with `v0.1.0`, 12.8 s with the rebuilt `:fastloop` image.
- App teardown (item 2), `HotelReservation.cleanup()`: 43.2 s normal, 17.9 s with fast namespace teardown.
- App redeploy (item 5), `fastloop up --redeploy`: 114.2 s with a labelled rebuild, 79.1 s when the unchanged build context skips the build. The rebuild here hit a warm BuildKit layer cache. In pipelines, `.sdo` commits invalidate `COPY .`, so the network `go get` and `go mod vendor` steps also rerun, and the saving is larger.

## Integration with `main` (`vic/feat/fastloop-integration`)

Inputs:
- Parent `vic/feat/fastloop` (`58c5b8a`, based on `8c83a04`) merged with GitHub `main` at `451c624`. That brings in `7c8ee0d`, `e949ca8`, `91b0080` and `451c624`.
- SREGym `vic/feat/fastloop-harness` (`7c1d48d7`) merged with `b4275585`, the submodule commit `main` pins (`e9631233` plus `dcbd087f` and `b4275585`).
- No conductor fix for missing mitigation verdicts had landed on `main` or the fork when this was cut. It merges independently later.

### Decisions

- **Fetched `main` from GitHub, not from the main checkout.** The main checkout's local `main` was behind (`91b0080`), and `origin` of this clone is that checkout. A `github` remote was added here; the main checkout was only read.
- **Textual conflicts: only the submodule pointer.** `main` changed only `incident_cost.py`, its test, `luna_reuse_DECISIONS.md` and the submodule; the fast loop touched none of the first three. The runner and adapter did not overlap: all of `main`'s concurrent-cluster isolation lives in the submodule.
- **SREGym merge was clean, but the fast-loop worker needed three semantic fixes** (`c53d5712`, test-first):
  - `main` replaced the conductor's `CLUSTER_BASELINE_STATE_FILE` constant with `cluster_baseline_state_file()`. The worker's per-run baseline override set the dead constant, so after the merge it would have silently used `~/cache_dir/cluster_baseline_state.fastloop-w0.json`. It now rebinds the function. Verified live: `up --redeploy` loaded `<run-dir>/cluster_baseline_state.json`, and no per-cluster file appeared in `~/cache_dir`.
  - The crossover guard. The Codex baseline's filtered proxy kubeconfig now goes through `verify_agent_kubeconfig` (only this proxy port; `kubectl get nodes` returns only `fastloop-w0-*`), at proxy start and again before every injection, as the conductor does. A mismatch fails the request.
  - `cluster_lock`. The worker holds the host-wide lock on `SREGYM_KIND_CLUSTER_NAME` for its lifetime, the same lock `parallel_runner` takes. A fast loop and an experiment, or two fast loops, can no longer drive one cluster at once.
- **The per-port agent kubeconfig does not apply to the fast loop.** It always passes an explicit per-run path (`results/<run-id>/agent.kubeconfig`) and an OS-assigned free port, never the default `/tmp/sregym-agent-kubeconfig-p<port>` or `16443 + id`. The SDO path uses the cluster's own kind kubeconfig and never the agent kubeconfig, so it needs no guard.
- **Private `/tmp` kept.** `main` moves fault-injector backups to `/tmp/sregym-<cluster>/`. Inside the worker's `bwrap` that resolves to `<run-dir>/tmp/sregym-fastloop-w0/`, which was confirmed live. The two layers compose; they do not conflict. The sandbox stays because other SREGym code still writes fixed `/tmp` names (TLS temp files, older injectors' legacy paths). The lock directory `~/.cache/sregym/locks` is outside `/tmp`, so the lock is host-wide inside the sandbox too.
- **`incident_cost.py`: deferred grading means zero judge wait** (`5b8e657`). With `diagnosis_grading_deferred = true`, `main`'s formula `fault_injected_at + TTL - diagnosis_submitted_at` came out as about 0 only because the conductor resets its clock right after injection, and it was unknown for rows without TTL. The marker now sets the wait to 0 explicitly. The analyze-experiment reference documents the judge-excluded and `applied_s` columns, which `e949ca8` had not.
- **Fixed pre-existing fast-loop check failures that `main` would have inherited:**
  - pyright: 24 errors, all in `benchmarks/sregym/fastloop` (`ed01c3f`); `main` has 0.
  - `test_architecture` facade violations (`029a421`). The adapter facade now lazily exports what the fast loop uses (PEP 562, as `libs/agent_cli` does), because adapter modules run as `python -m` entry points, one of them in the responder image. `python -W error -m benchmarks.sregym.adapter.persistent --help` stays clean.
- **Fixed a fast-loop bug found by the smoke run** (`472790d`). `run --agent codex` with a new run id failed with `FileNotFoundError`, because the results directory was created after the worker was asked to write the agent kubeconfig into it.
- **`SDO_GO_CACHE_SEED` stays unconditional, and images must be rebuilt at merge.** The controller Job now always sets it, and `go_runner` (already in `v0.1.0` images) raises if the directory is missing. `sdo-controller:v0.1.0` has no `/opt/sdo/go-build-cache` (checked with `docker history`), so merged host code with the current `v0.1.0` controller image fails at the in-pod detector build.
  - Alternative: gate the env behind a flag. Rejected: it would make the 85 s -> 4 s gate saving opt-in forever. Images are already rebuilt by hand (`scripts/build_sdo_images.sh`) whenever in-pod code changes, and the failure is immediate and explicit.
- **Pushed the submodule branch to the fork, and fetched it into the shared submodule object store** (refs and objects only; the main checkout's working tree and `third_party/sregym` checkout were not touched).

### Checks (integration head)

- `format_code.sh`, `check_errors.sh` (ruff, tach), pyright (0 errors) and `check_arch.sh` all pass.
- Unit suite: 1614 passed, 2 skipped.
- Go `controller/sdk`, `core` and `runtime`: ok.
- SREGym:
  - The fast-loop worker, deferred grading, fast teardown, source deploy, cluster isolation, proxy config and parallel runner tests: 49 passed.
  - Full `tests/` (without `file_editing`, which fails to import on the base too): 681 passed and 5 failed. The same 5 fail without these changes; they need a live kubeconfig or cluster.

### Smoke run on `fastloop-w0` (images rebuilt from the integration head with the `fastloop` builder)

- Images: `sdo-controller:fastloop` `e3dc19ec442a` (3.27 GB; `v0.1.0` is 2.26 GB), `sdo-sregym-responder:fastloop` `559878fbe682`, `sdo-responder:fastloop` `8c5ff027e243`, `sdo-detector-validator:fastloop` `5176f1af3dbc` (cache hit). The `v0.1.0` tags are unchanged.
- Sequence: `down` (drained the controller running the previous `:fastloop` image), then `up` (44 s, deploy skipped), then `up --redeploy` (91 s, deploy 50 s), then Codex, then SDO. Codex ran first, because a live SDO controller would repair the baseline's fault.

| Run | Incident | Wall (s) | Injection to mitigation (s) | Injection to verified (s) | Gate (s) | Setup (s) | Reflection (s) | Responder tokens | Oracle |
|---|---|---|---|---|---|---|---|---|---|
| `integ-codex-1` | 0 | 85.3 | 80.7 | 83.0 (Codex exit) | - | - | - | 345,559 | pass |
| `integ-sdo-2` (fresh install) | 0 | 94.8 | 38.5 | 63.5 | 5.1 | 15.0 | 6.7 | 192,455 | pass |
| `integ-sdo-2` | 1 | 94.0 | 30.9* | 73.7* | 1.1 | 0.9 | 3.5 | 168,504 | pass |

- Both SDO incidents took the warm path (exact fingerprint) with zero reflection tokens. The lifecycle was satisfied by `workspace-attestation`, since the workspace already carried the attestation from `sdo-cachehit-1`.
- The Codex guard logged `verified: port 39327 reaches only fastloop-w0` twice, at proxy start and before injection.
- \* Incident 1's `incidents.jsonl` row has no detection, mitigation or verification timestamps. Its reflection finished within one poll of verification, so `_wait_for_verified_incident` took its existing "reflection finished between polls" branch (`closure=None`). The times above come from the receipt (repair `completed_at`, driver `injection_to_verified_recovery`; `incident_resolution_seconds` 78.3). This adapter behavior predates the merge and is on `main` too; it is now likelier because warm-path reflection is short. Left as a known gap: fixing it means reading the closure back from the ledger, which is outside this integration.
