# Fast SDO experiment iteration: decisions log

Goal: cut the SREGym SDO development loop from about 20 minutes per two-round pipeline (under 3 minutes of which is the agent working on the incident) to a few minutes per incident. Owner: autonomous agent. Every entry states what was chosen, the alternatives, and why.

Reference run: `third_party/sregym/logs/20260927_174023_pipeline_sdo-codex-luna-persistent` (2 rounds of `missing_configmap_hotel_reservation`, persistent controller, kind `luna-w0`).

## Working environment

- **Separate clone, not a worktree of the main checkout.** This agent was sandboxed to a worktree of the SREGym submodule, and `git worktree add` against the main checkout would write into its `.git`. The parent work is in a clone at `/mnt/data/shli/sdo-fastloop`, on branch `vic/feat/fastloop`, based on `main` at `8c83a04`. Its `third_party/sregym` is a clone of the shared submodule repository. The submodule branch `vic/feat/fastloop-harness` is based on `38cbf4c7`, the commit `main` pins, not the worktree's default `180091bc`. When finished, the branch is fetched back into the shared submodule repository so it can be integrated.
- **Images are re-tagged, not rebuilt.** `sdo-controller`, `sdo-responder`, `sdo-sregym-responder` and `sdo-detector-validator` got an extra `:fastloop` tag on the image IDs that `:v0.1.0` pointed to at 18:55 (`8173e51e7c3a`, `0c0946f1c335`, `efeea534d8c2`, `bb1ff6241ceb`). The shared tags are untouched.
  - Alternative: rebuild from the clone. Rejected: nothing in the fast loop changes image contents, and a rebuild costs CPU that would disturb the concurrent timed runs.
- **Own cluster `fastloop-w0`**, created through SREGym's own `create_worker_cluster` with prefix `fastloop-w`. It uses the same kind config, Calico enforcement, and `worker_cpu_limit = 3` as the `luna-*` runs, so incident timings are comparable.
- **No shared buildx builder.** The hotel image is built with the default Docker builder, not `sdo-example`, so this work never touches the timed runs' builder state or cache.

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
