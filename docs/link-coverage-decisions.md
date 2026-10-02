# Link coverage backstop: decisions

Branch `vic/fix/link-coverage-backstop`, from `vic/exp/mixed-integration` (`7009a943`), 2026-10-02.

## Why

Step 2 of the mixed-stream confirming run showed the health judge's `links.yaml` is nondeterministic across cold lifecycles. The cold C1 lifecycle listed only `frontend -> consul` and `search -> consul`; the cold single-fault lifecycle listed five frontend edges. With no edge to `recommendation`, an isolating policy on it was never detected, no responder dispatched, and the official oracle failed the incident. Which edges exist must not depend on what a model happens to list.

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | **(a) Derive a second link workload from topology, in the lifecycle.** `workloads/topology-links.yaml` holds one edge per TCP port of every core `v1` Service in the tracked manifests, `from: sdo-prober`. | (b) a validator rule that rejects a judge `links.yaml` with no inbound edge for each Service the objective names. (c) discovery inside the prober or controller at run time. | (b) still depends on a model: the manifest inventory has no call edges and no ports (its dependencies are selectors, labels and ConfigMaps), so the rule could not say which edge or port is missing, and a judge round only retries. (c) needs Kubernetes access in the prober, which is deliberately credential-free and compiled from the committed workloads, or a new controller-to-prober write API. (a) is deterministic, uses only what the source declares, and flows through the existing installer and detector. |
| 2 | **Keep the judge's `links.yaml`.** Both workloads run. | Replace the judge's list by the derived one. | The judge's edges carry the real caller as the finding's related Service and can name edges the manifests do not (a port taken from code). The derived list is a floor, not a replacement. |
| 3 | **`from` is `sdo-prober`** (`traffic.ProberSource`); a finding for such an edge relates no calling Service and its evidence does not say "connections from X". | An arbitrary real Service as a label; making `from` optional (new ID shape). | The prober dials by itself, so any real caller would be false. A constant keeps the rule ID shape (`link-reachability.sdo-prober.<to>.<port>`) and the validation unchanged. |
| 4 | **`topology-*` is lifecycle-owned.** It is excluded from the judge's authored set (`_JUDGE_TRAFFIC_FILE`), so a judge round neither sees nor deletes it, and a judge-authored `topology-*` file is rejected. Responders were already limited to `incident-*`. | Keep it under the judge's ownership. | Otherwise a judge round (`_write_traffic_files` clears the judge-owned set) could drop it, and `_traffic_errors` would reject its `from`, which is not a source-backed Service. |
| 5 | **Regenerated in `_ensure_generic_health_detectors`**, on every initial lifecycle, refresh and reuse; removed when the source has no Service. | Generate once at lifecycle time. | Existing attested seeds pick it up on their next `ensure`, and edges follow the source topology. |
| 6 | **Skip** ExternalName Services, Services without a core-`v1` TCP port, non-core `Service` kinds (Knative), and a Service named like the prober. Dedupe the same name and port across deployment variants. | Probe everything, filter by the active variant. | A dial of the skipped kinds says nothing. The active variant is not available at the ensure call sites; an undeployed variant's edge never connects and the detector ignores an edge that never connected. |
| 7 | **Cap at the link-probe limit (64), sorted by Service and port**, with a log line when it truncates. | Raise `MaxLinks`; sample. | A deterministic cap keeps probe volume bounded (one fresh dial per edge per second from one pod) and replays the same on every lifecycle. A very large application (train-ticket has dozens of Services; Helm-templated manifests do not parse as YAML) is covered partially. The judge's list is separate and has its own 64. |
| 8 | **Link-only probers.** `prober.New` builds with an empty catalog when only link workloads exist; the builder no longer requires generators for `link-probe` workloads (scenario workloads still do); the generated conformance test skips an empty catalog. | Emit the derived workload only when the judge authored generators. | An application with nothing HTTP to probe is exactly where scenario probes cannot help and the link probe can. Without this change the derived workload would have failed the controller build for any application with no generators. |
| 9 | **False-positive guard unchanged**: an edge reports only after it has connected at least once and then failed `failures` consecutive fresh dials. | Report never-connected edges. | Services with no ready endpoints, headless Services whose pods are not up, ports nothing listens on, and Services the prober is not admitted to stay silent. |

## Residual risks

- A fault that is already present when the lifecycle's prober first starts (the edge never connected) is not reported. The prober-warm gate (`ce7663db`) covers injection after warm-up.
- A Service declared only in Helm templates, a Kustomize overlay or a generator is not in the tracked YAML, so it gets no derived edge; only the judge's edges cover it.
- Edges beyond the cap are not probed. For very large applications the first 64 `(Service, port)` pairs in sorted order are.
- A TCP dial succeeding says nothing about the application protocol; the scenario probes still own that.
- A Service whose pods are deliberately scaled to zero after having connected reports a finding. That is a real loss of reachability, and the endpoints detector reports it too.
- Probe volume: up to 64 fresh one-second dials plus the judge's edges, from one pod, inside the application namespace.

## Confirming run needed

1. **Cold C1 through the conductor** (`confirm_c1.toml`, gate on, luna judge, the images from this branch): `.sdo/diagnostics/traffic/workloads/topology-links.yaml` exists in the committed `.sdo` and lists `recommendation:8085` (and every other hotel Service port); `traffic-topology-links` is registered; the fault gate waits for the prober; a `link-reachability.sdo-prober.recommendation.8085` finding fires within seconds of injection (before any responder dispatch), and the official oracle passes. Repeat once with a lifecycle whose `links.yaml` omits `recommendation`, which is the failure being fixed.
2. **Healthy window** (at least 10 minutes, no injection, same lifecycle): zero active findings from `traffic-topology-links` and `traffic-links`, with the load average logged. Any finding must be classified (a real flap or a false positive).
3. A non-HTTP application (no generators) builds a controller and its prober starts with link workloads only.
