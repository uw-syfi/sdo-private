# N-fault composites: decisions and results (in progress)

Status: 2026-10-01, work in progress. This file is updated as arms finish. Branches: parent `vic/exp/nfault-composites` (from `vic/exp/hand-composites`, merged `vic/exp/late-fire-integration`), submodule `vic/exp/nfault-composites`. Predecessor: `docs/hand-composites-decisions.md` (2-fault composites).

## Hypothesis and what was built

Hypothesis: with many simultaneous faults SDO resolves more of them than a one-shot or verify-loop Codex, because its health detector keeps the loop running and learned detectors route to each fault. Fair control: Codex gpt-6-luna with the opt-in verify protocol (`benchmarks/sregym/runner/codex_baseline.py`, `VERIFY_PROTOCOL_PROMPT`) plus plain memoryless Codex (new incident set).

Hand-registered composites on Hotel Reservation (`ComposedFailures`, submodule `composed_failures.py`; raised the cap from 3 to 5 faults; one fault per Deployment, no call-graph dependency between targets):

| id | faults (injection order) |
|---|---|
| `composite3_hotel_geo_rate_recommendation` | readiness probe (geo), missing ConfigMap (mongodb-rate), network policy block (recommendation) |
| `composite5_hotel_geo_rate_recommendation_frontend_user` | the three above, plus wrong service selector (frontend), oversized memory request (user) |

Tooling added (parent repo, all with tests): per-fault read-only probes polled during the run (`fastloop/fault_tracker.py`), a multi-incident SDO agent for composites and a wrapper that tracks the Codex arms (`fastloop/composite.py`), `collect_followup_incident`, `DetectorReviewRequiredError` and an optional wait-out mode in `adapter/persistent.py`, and a per-run table (`benchmarks/sregym/analysis/composite_table.py`). Hand-registered only; no generic stream generator.

## Decisions

1. **Fifth fault is `resource_request_too_large(user)`, not `(reservation)`.** Hotel Reservation has no `reservation` Deployment (the first no-LLM verify silently injected nothing). `user` has no dependency on the other targets. The injector's recovery also read its backup from `/tmp` while the injector wrote it to the per-cluster scratch directory, so recovery deleted `user` and never recreated it; fixed in the submodule (test included).
2. **NetworkPolicyBlock now replaces an existing policy of the same name.** Found when a warm repeat failed with HTTP 409: the responder had committed a permissive `deny-all-recommendation` manifest to the source tree, the redeploy applied it, and the next injection could not create it.
3. **All arms use the fast loop** (`benchmarks.sregym.fastloop`), not the SREGym conductor. It grades the live cluster with the problem's own oracle, so there is no LLM judge and TTD/TTM contain no judge time by construction. The consequence is that diagnosis is not graded by a judge; a fault counts as "found" when its probe is green and the agent's own diagnosis/repair names it (listed below).
4. **Per-fault resolution comes from read-only kubectl probes, not from the SREGym sub-oracles.** The sub-oracles spawn helper pods in the application namespace and wait up to 60 s, which would disturb both agents. The official oracle still grades the end state once. The two agree on the count except where an agent returned within seconds of its last fix (see caveats); tables count a fault resolved at the start of its final green run.
5. **The SDO composite agent keeps the controller running after the first verified incident** (`pause_after_verified=False`), drains each incident's reflection, and waits for the next incident until all faults are green, no incident arrives in 5 minutes, or a deadline passes. This is what the hypothesis needs; it turned out not to be exercised (see canary).
6. **Stop rule.** When the controller state reports `detector_review_required`, the run stops (it never closes the incident). A `--no-stop-on-review` mode waits out a 25 minute cap instead, to test that the stop rule is not cutting SDO short.
7. **Images.** Early SDO runs used `lf1` images; the late-findings pull arm needs the merged head, so `nf1` images were built from this branch (`SDO_IMAGE_TAG=nf1 scripts/build_sdo_images.sh`) and all later SDO arms use `nf1`. The `lf2` images lack the broker `--late-findings-log` flag the merged head passes and fail at dispatch (`workspace_pending`), so they cannot be used with this tree.
8. **Cluster state hygiene.** The responder may permanently change the live Deployment (for example, removing mongodb-rate's ConfigMap mount), which makes later injections of the same fault inert. Every SDO run therefore starts on a freshly redeployed app (`up --redeploy`), and the tracker records `ever_red` per fault to flag an inert injection. One early replicate was discarded for this reason.

## Canary: what SDO does with a 3-fault composite (no-LLM verify passed first)

No-LLM verify (`/mnt/data/shli/nfault-runs/verify-nfault.jsonl`): both composites inject, every sub-oracle and probe goes red, reference recovery turns all green (3-fault recovery 58 s; 5-fault 10 s after the fixes).

Observed (controller logs, runtime state, detector firing telemetry):

- **One incident, not several.** The health detector fires on geo first; the batcher dispatches about 0 to 10 s after the first finding. The other faults surface later (mongodb-rate about 7 s, network policy about 15 s after the first). The responder is dispatched with the geo findings only. The 3 and 5 fault runs both show `before_dispatch` for geo and `after_dispatch` for everything else.
- **The responder fixed more than it was asked.** It explores the namespace and repaired mongodb-rate as well (and, in the 5-fault run, user and the frontend selector), but never the network policy.
- **The controller does not keep dispatching.** After the responder completed, health detectors stayed active (the network policy finding), so after the 2 minute verification timeout the controller recorded `detector_review_required`, exited with `detector review required: ...`, and the Job restarted it; every restart read the same persisted state and exited again (6 pods in 13 minutes in the first canary). No second responder was ever dispatched and the incident never closed, so there was no reflection and nothing learned. With partial resolution the controller is wedged, not looping.
- The run is therefore a loss for the hypothesis in this implementation: the health detector keeps the incident open but nothing re-dispatches for the residual finding.

## Results (fault resolution; seconds from end of injection)

Filled in below as arms complete; see the generated tables.

RESULTS_PLACEHOLDER

## Takeaways

TAKEAWAYS_PLACEHOLDER

## Caveats

- n is small (2 per Codex arm, 1 to 3 per SDO arm); stochastic agents; shared host (load logged in `/mnt/data/shli/nfault-runs/load.log`).
- No LLM judge: "found" is operational (probe green and named in the agent's own text), not judge-graded.
- Health detectors are generated per run by the lifecycle, so detection order differs between SDO runs and affects what the first dispatch contains.
- Fast loop, not the SREGym conductor: no namespace teardown between incidents, host Codex CLI for the Codex arms.
- Git: commits and pushes succeeded after an initial transient classifier denial of one combined commit command.
