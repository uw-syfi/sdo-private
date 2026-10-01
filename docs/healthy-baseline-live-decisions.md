# Healthy-baseline gate, live wiring: decisions and results

Status: 2026-10-01, in progress. Branch `vic/exp/healthy-baseline-live` = `vic/fix/detector-healthy-baseline` merged with `vic/exp/cold-c2-selector-learning`. Worktree `/mnt/data/shli/sdo-worktrees/hb-live`; `third_party/sregym` is a copy of the simul2 checkout (the composite3b commit `28ab2497` exists only in the local submodule state; no remotes changed). Predecessors: `docs/detector-healthy-baseline-decisions.md` (the gate), `docs/cold-c2-selector-learning-decisions.md` (the finding).

## Questions

1. Does the gate reject the reflection-created selector detector that false-fired on four healthy jaeger services in sel3-b, with an actionable message, while the genuinely useful seeded detector (selw-a/b/c `service_selector_mismatch`) still passes?
2. Can the snapshots be captured and supplied in a live run without touching `controller/runtime`?
3. (Only if images can be rebuilt) With the gate on, is the false-firing detector rejected or never created in a live C3, C3 (label reset), C1 sequence, and does the run complete?

## Decisions

- **Capture point and place (benchmark adapter, not `controller/runtime`).** `benchmarks/sregym/adapter/healthy_baseline.py` converts `kubectl get configmaps,services,pods,deployments,replicasets,endpoints,endpointslices,networkpolicies,events -o json` into the `sdktest.Snapshot` shape (other kinds, so Secrets, are dropped; `managedFields` stripped), takes 3 snapshots 2 s apart, and writes them with `kubectl exec -i job/sdo-controller-run -c controller -- python3 -c ...` onto the repository volume at `/workspace/.sdo-baseline/healthy` (outside the application repository, so never a memory artifact). The ClusterOps wrapper `HealthyBaselineCaptureOps` wraps the stage's `inject` callback, so it captures immediately before the fault lands: after the controller's all-clear in the default gate, and while the controller is paused with the application deployed but unfaulted under `--inject-before-resume` (the composite arm, where there is no all-clear evaluation to wait for). A failed capture aborts before injection (fail loud, never silently ungated). Each stage replaces the previous snapshots.
- **Staging into the validated tree (broker, not the harness).** The Kubernetes validator Job mounts only the worktree, and the broker computes changed paths from `git ls-files --others`, so untracked baseline files cannot simply be left in the worktree: `.sdo-baseline/` would be rejected as a path outside `.sdo`. `MemoryValidator(healthy_baseline_source=..., healthy_baseline_dir=...)` instead copies the `*.json` files into the worktree only for the executable gate and removes them (and the empty parent) in a `finally`. Broker flags: `--healthy-baseline-source` (volume path) with the earlier `--healthy-baseline-dir` (worktree-relative). The gate runs only when `.sdo/diagnostics/` changed, as before. A missing or empty source fails validation with an explanatory message.
- **Opt-in surfaces, default off.** `ControllerInstallConfig.healthy_baseline: bool = False` (adds the two broker arguments; absent otherwise, so the controller arguments are byte-identical), SREGym `agent_config.sdo_codex.healthy_baseline` / `--healthy-baseline` in `benchmarks/sregym/adapter/driver.py` (persistent mode wraps the ClusterOps; job mode records in the fault gate), fastloop `run --healthy-baseline`.
- **Message dedupe.** The first offline run showed one violation per matching Deployment (19 identical lines per service, 228 in all) because the detector emits a finding per Deployment. `HealthyBaselineViolations` now reports each distinct (snapshot, rule, resource) once.
- **Rebuild needed.** Controller image (the `sdo` package with the broker and validator), validator image (Go `sdktest` baseline code and `controller.builder`), and responder images only if they embed the changed Python.

## Offline experiment (no cluster created)

Detectors: the false-firing artifact is `.sdo/diagnostics/detectors/incidents/service_selector_ready_pod_mismatch` (`service-selector-ready-pod-mismatch`) at HEAD of `/mnt/data/shli/clc-runs/sel3-b/application_workspace` (introduced by commit `011d650`, the first C3 reflection of that run). The useful detector is `service_selector_mismatch` at HEAD of `/mnt/data/shli/clc-runs/selw-a/application_workspace`. The `.sdo` of each was exported with `git archive HEAD .sdo`.

Healthy snapshots: none were stored in the run directories, so three were captured read-only with the new converter from a quiet, fault-free hotel-reservation namespace (kind cluster `fastloop-w0`: 19 deployments, 20 running pods, 23 services, no NetworkPolicy; `jaeger`, `jaeger-agent`, `jaeger-collector`, `jaeger-query` have selectors `app: jaeger*` while the one jaeger pod is labelled `io.kompose.service: jaeger`, which is exactly the shape that false-fired). Files: `/mnt/data/shli/clc-runs/hb-live/healthy/healthy-{0,1,2}.json` (298 KB each); gate logs `gate-sel3b.txt`, `gate-selwa.txt` (and the pre-dedupe `gate-sel3b-nogo.txt`) beside them.

Command (the validator's own entry point, with the gate on):
`python -m controller.builder.check_cli test --app <ws> --healthy-baseline .sdo-baseline/healthy`

| detector | its own Go tests | healthy-baseline gate | exit |
|---|---|---|---|
| sel3-b `service-selector-ready-pod-mismatch` | ok | REJECTED: 12 violations, 4 services x 3 snapshots: jaeger, jaeger-agent, jaeger-collector, jaeger-query | 1 |
| sel3-b other detectors (`required_configmap_missing`, `total_isolation_network_policy`, `transient_dependency_startup`) | ok | quiet | |
| selw-a `service_selector_mismatch` (with `empty_network_policy`, `missing_configmap`) | ok (its positive and near-miss tests) | quiet on all three snapshots | 0 |

The rejection message names the detector, snapshot, rule, resource and remedy: `detector "service-selector-ready-pod-mismatch" reported an active finding on healthy baseline snapshot healthy-0.json (namespace "hotel-reservation"): rule "selector-excludes-ready-pod" on Service hotel-reservation/jaeger: A Service selector excludes a Ready workload pod; the healthy baseline is a recorded cluster state with no fault, so an incident detector must stay quiet on it; key the predicate on the fault condition itself (a state the healthy application never has) instead of a shape that ordinary healthy resources also have`. The four rejected services are the same four that false-fired in sel3-b and sim-a, and all of the detector's own tests passed, which reproduces the blind spot the gate targets.

(live-run section follows)
