# N-fault composites: decisions and results

Status: 2026-10-01, all planned arms finished. Branches: parent `vic/exp/nfault-composites` (from `vic/exp/hand-composites`, merged `vic/exp/late-fire-integration`), submodule `vic/exp/nfault-composites`. Predecessor: `docs/hand-composites-decisions.md` (2-fault composites).

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


### 3-fault composite

| arm | runs | all faults resolved | mean faults resolved | median s to last resolved fault (solved runs) | mean tokens (agent+reflection) |
|---|---|---|---|---|---|
| SDO as-is (cold) | 3 | 0/3 | 2.0/3 | - | 1.38M |
| SDO as-is, stop rule off, 25 min cap | 1 | 0/1 | 2.0/3 | - | 1.25M |
| SDO + pull late findings (cold) | 2 | 2/2 | 3.0/3 | 60 | 1.16M |
| SDO + follow-up responders (cold) | 3 | 2/3 | 2.7/3 | 251 | 0.92M |
| Codex plain | 4 | 0/4 | 2.0/3 | - | 0.41M |
| Codex + verify | 4 | 0/4 | 1.8/3 | - | 0.45M |

Per-fault resolution counts (runs where the fault was resolved / runs where its injection was effective):

| arm | configmap (mongodb-rate) | network_policy (recommendation) | readiness (geo) |
|---|---|---|---|
| SDO as-is (cold) | 3/3 | 0/3 | 3/3 |
| SDO as-is, stop rule off, 25 min cap | 1/1 | 0/1 | 1/1 |
| SDO + pull late findings (cold) | 2/2 | 2/2 | 2/2 |
| SDO + follow-up responders (cold) | 3/3 | 2/3 | 3/3 |
| Codex plain | 4/4 | 0/4 | 4/4 |
| Codex + verify | 3/4 | 0/4 | 4/4 |

Per run (seconds from end of injection to the start of each fault's final green run; `-` = never resolved):

| arm | run | resolved | stop | per-fault resolution s | tokens | inert injection |
|---|---|---|---|---|---|---|
| SDO as-is (cold) | asis-c3-r2 #0 | 2/3 | detector_review_required | configmap 130, network_policy -, readiness 37 | 1.08M | - |
| SDO as-is (cold) | asis-c3-nf1 #0 | 2/3 | no_further_incident | configmap 135, network_policy -, readiness 57 | 1.94M | - |
| SDO as-is (cold) | asis-c3-nf1-b #0 | 2/3 | detector_review_required | configmap 176, network_policy -, readiness 57 | 1.13M | - |
| SDO as-is, stop rule off, 25 min cap | asis-c3-nostop2 #0 | 2/3 | no_closure_within_deadline | configmap 130, network_policy -, readiness 57 | 1.25M | - |
| SDO + pull late findings (cold) | pull-c3-r2 #0 | 3/3 | all_faults_resolved | configmap 62, network_policy 47, readiness 57 | 1.14M | - |
| SDO + pull late findings (cold) | pull-c3-r3 #0 | 3/3 | all_faults_resolved | configmap 57, network_policy 47, readiness 47 | 1.18M | - |
| SDO + follow-up responders (cold) | followup-c3-r62 #0 | 3/3 | all_faults_resolved | configmap 88, network_policy 238, readiness 47 | 1.05M | - |
| SDO + follow-up responders (cold) | followup-c3-r63 #0 | 2/3 | no_further_incident | configmap 135, network_policy -, readiness 62 | 1.19M | - |
| SDO + follow-up responders (cold) | followup-c3-r61 #0 | 3/3 | all_faults_resolved | configmap 130, network_policy 264, readiness 47 | 0.52M | - |
| Codex plain | plain-c3 #0 | 2/3 | - | configmap 57, network_policy -, readiness 78 | 0.49M | - |
| Codex plain | plain-c3 #1 | 2/3 | - | configmap 57, network_policy -, readiness 78 | 0.46M | - |
| Codex plain | plain-c3b #0 | 2/3 | - | configmap 62, network_policy -, readiness 52 | 0.23M | - |
| Codex plain | plain-c3b #1 | 2/3 | - | configmap 62, network_policy -, readiness 72 | 0.44M | - |
| Codex + verify | verify-c3 #0 | 2/3 | - | configmap 124, network_policy -, readiness 88 | 0.50M | - |
| Codex + verify | verify-c3 #1 | 2/3 | - | configmap 109, network_policy -, readiness 98 | 0.51M | - |
| Codex + verify | verify-c3b #0 | 2/3 | - | configmap 135, network_policy -, readiness 114 | 0.50M | - |
| Codex + verify | verify-c3b #1 | 1/3 | - | configmap -, network_policy -, readiness 88 | 0.30M | - |

### 5-fault composite

| arm | runs | all faults resolved | mean faults resolved | median s to last resolved fault (solved runs) | mean tokens (agent+reflection) |
|---|---|---|---|---|---|
| SDO as-is (cold) | 2 | 0/2 | 4.0/5 | - | 1.45M |
| SDO + follow-up responders (cold) | 1 | 1/1 | 5.0/5 | 520 | 1.09M |
| SDO + pull late findings (cold) | 1 | 1/1 | 5.0/5 | 117 | 3.07M |
| SDO + pull late findings (warm, 2nd run, same controller) | 1 | 1/1 | 5.0/5 | 80 | 0.47M |
| Codex plain | 4 | 0/4 | 3.5/5 | - | 0.49M |
| Codex + verify | 4 | 0/4 | 3.8/5 | - | 0.64M |

Per-fault resolution counts (runs where the fault was resolved / runs where its injection was effective):

| arm | configmap (mongodb-rate) | network_policy (recommendation) | readiness (geo) | resource_request (user) | wrong_selector (frontend) |
|---|---|---|---|---|---|
| SDO as-is (cold) | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| SDO + follow-up responders (cold) | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| SDO + pull late findings (cold) | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| SDO + pull late findings (warm, 2nd run, same controller) | 1/1 | 1/1 | 1/1 | 1/1 | 0/0 |
| Codex plain | 4/4 | 0/4 | 4/4 | 4/4 | 0/2 |
| Codex + verify | 4/4 | 0/4 | 4/4 | 4/4 | 1/2 |

Per run (seconds from end of injection to the start of each fault's final green run; `-` = never resolved):

| arm | run | resolved | stop | per-fault resolution s | tokens | inert injection |
|---|---|---|---|---|---|---|
| SDO as-is (cold) | asis-c5-r1 #0 | 4/5 | detector_review_required | configmap 117, network_policy -, readiness 21, resource_request 91, wrong_selector 186 | 1.09M | - |
| SDO as-is (cold) | asis-c5-nf1 #0 | 4/5 | detector_review_required | configmap 233, network_policy -, readiness 80, resource_request 154, wrong_selector 254 | 1.81M | - |
| SDO + follow-up responders (cold) | followup-c5-r62 #0 | 5/5 | all_faults_resolved | configmap 233, network_policy 381, readiness 48, resource_request 175, wrong_selector 520 | 1.09M | - |
| SDO + pull late findings (cold) | pull-c5-r1 #0 | 5/5 | all_faults_resolved | configmap 117, network_policy 69, readiness 69, resource_request 69, wrong_selector 101 | 3.07M | - |
| SDO + pull late findings (warm, 2nd run, same controller) | pull-c5-warm #0 | 5/5 | all_faults_resolved | configmap 80, network_policy 80, readiness 80, resource_request 80, wrong_selector 0 | 0.47M | wrong_selector |
| Codex plain | plain-c5 #0 | 3/5 | - | configmap 90, network_policy -, readiness 64, resource_request 53, wrong_selector - | 0.70M | - |
| Codex plain | plain-c5 #1 | 3/5 | - | configmap 101, network_policy -, readiness 111, resource_request 53, wrong_selector - | 0.39M | - |
| Codex plain | plain-c5b #0 | 4/5 | - | configmap 116, network_policy -, readiness 169, resource_request 154, wrong_selector 0 | 0.53M | wrong_selector |
| Codex plain | plain-c5b #1 | 4/5 | - | configmap 90, network_policy -, readiness 58, resource_request 48, wrong_selector 0 | 0.35M | wrong_selector |
| Codex + verify | verify-c5 #0 | 3/5 | - | configmap 116, network_policy -, readiness 212, resource_request 90, wrong_selector - | 0.52M | - |
| Codex + verify | verify-c5 #1 | 4/5 | - | configmap 95, network_policy -, readiness 127, resource_request 95, wrong_selector 90 | 0.59M | - |
| Codex + verify | verify-c5b #0 | 4/5 | - | configmap 117, network_policy -, readiness 122, resource_request 69, wrong_selector 0 | 0.89M | wrong_selector |
| Codex + verify | verify-c5b #1 | 4/5 | - | configmap 116, network_policy -, readiness 138, resource_request 69, wrong_selector 0 | 0.59M | wrong_selector |

Notes on the tables. Network policy is the discriminating fault: it is the one every Codex run (16 of 16) and every as-is SDO run left unresolved. Codex and as-is SDO fix the three or four faults they happen to find while exploring the namespace and then stop. `inert injection` marks a fault whose probe was never red (warm or Codex-b runs on a cluster whose app had been edited earlier); those count as not applicable in the per-fault denominators, which is why wrong_selector shows 0/2 for plain Codex.

### Who found each fault (from logs and detector telemetry)

- configmap (mongodb-rate): fixed by the first responder in every SDO arm, and by Codex in 7 of 8 c3 runs (the verify-c3b #1 run never fixed it). Not found by a detector first; the responder found it by exploring after the geo task.
- readiness (geo): the only fault in the first dispatch's task (health detector fired on geo first). Resolved by the first responder everywhere.
- resource_request (user) and wrong_selector (frontend), 5-fault: found by exploration of the first responder in as-is and Codex runs (4/5), in pull and follow-up runs also by the first responder.
- network_policy (recommendation): never fixed by any Codex run or as-is SDO run. In the pull arm the first responder pulled the late recommendation finding (47 to 69 s). In the follow-up arm a second responder was dispatched on the residual finding (first at about 211 s, tokens 465k + 583k in r62; 281k + 237k in r61; in c5 the last fault at 520 s after three incident requests).

### pull-c3-r1 (the 1212 s oracle pass the coordinator asked about)

pull-c3-r1 ran before the per-fault tracker existed, so it has no per-fault table; the log gives inj->mit 222.5 s (all three oracle conditions green) and wall 1212 s. The long wall time is the post-mitigation phase (verification, 49 s gate, then reflection with 588k reflection tokens) not resolution time. What the pull arm did differently: the responder pulled the findings activated after dispatch (the recommendation network policy) into its task, so it addressed the policy explicitly. Not luck: the two tracked repeats (r2, r3) resolved all three faults in 62 s and 57 s, and the 5-fault pull run also resolved 5/5 (117 s). The pull arm is n=4 total (3 c3 runs including r1, 1 c5 cold, 1 c5 warm) all successful.

### Comparison against Codex

3-fault: Codex plain 0/4 all-resolved (2.0/3 mean), Codex + verify 0/4 (1.8/3), SDO as-is 0/3 (2.0/3), SDO + pull 2/2 tracked (plus r1, 3/3), SDO + follow-up 2/3 (2.7/3). 5-fault: Codex plain 0/4 (3.5/5), Codex + verify 0/4 (3.8/5), SDO as-is 0/2 (4/5), pull 2/2, follow-up 1/1. SDO arms cost 0.9 to 1.4M tokens per run vs 0.4 to 0.6M for Codex.


## Takeaways

1. **As shipped, the hypothesis is false.** As-is SDO (0/3 on c3, 0/2 on c5) is no better than Codex (0/8 on c3 and c5 combined for each Codex arm): it solves the same faults Codex solves by exploration, then wedges on detector review (or silently closes) instead of dispatching for the residual. Confidence: medium-high for c3 (7 SDO and 8 Codex runs per fault outcome, all consistent); lower for c5 (2 SDO runs). Implication: the health detector keeping the incident open is not enough; nothing re-dispatches. Next step: ship late-findings pull or follow-up responders as the default.
2. **With either fix SDO beats Codex on the discriminating fault.** Pull: 5/5 runs tracked or not resolve every fault, typically within 120 s. Follow-up: 3 of 4 runs fully resolved (c3 2/3, c5 1/1), slower (network policy at 238 to 520 s) and via a second responder. Confidence: medium (n=2 to 4 per arm, one host, no judge). Implication: the mechanism that matters is routing late findings to a responder, either into the first task (pull) or as a new one (follow-up); the learned-detector claim is not tested here because cold runs dominate. Next step: more repeats on c5 and a 4-fault variant, and a warm run of follow-up.
3. **Codex plus verify does not help with compositions.** The verify loop checks the incident it was told about; it was no better than plain Codex (1.8/3 vs 2.0/3; 3.8/5 vs 3.5/5, within noise) and never found the network policy. Confidence: medium. Implication: a verify prompt is not a substitute for a standing health detector. Next step: a Codex arm whose verify step is the full application health probe would be the fairer control.
4. **Pull vs follow-up.** Pull is faster and cheaper per resolved fault, follow-up is more robust to a responder that closes its task early but depends on controller state (one silent closure on lane 63: the lifecycle-refreshed health detector set lost the network policy rule, so no finding existed to follow up on). Confidence: low-medium, one silent closure. Next step: keep the network policy rule when the lifecycle refreshes health detectors, and test whether pull also suffers from it.
5. **Warm runs.** The one warm pull c5 run resolved 5/5 in 80 s with 0.47M tokens, a hint that memory helps, but wrong_selector was inert in that run so it is weaker evidence. Not conclusive.


## Caveats

- n is small (2 per Codex arm, 1 to 3 per SDO arm); stochastic agents; shared host (load logged in `/mnt/data/shli/nfault-runs/load.log`).
- No LLM judge: "found" is operational (probe green and named in the agent's own text), not judge-graded.
- Health detectors are generated per run by the lifecycle, so detection order differs between SDO runs and affects what the first dispatch contains.
- Fast loop, not the SREGym conductor: no namespace teardown between incidents, host Codex CLI for the Codex arms.
- Git: commits and pushes succeeded after an initial transient classifier denial of one combined commit command.
- Images differ by arm: lf1 (first as-is runs), nf1 (pull and later as-is), nf3 (follow-up arm; nf2 exited because `controller/builder/check_cli.py` rejected `--max-follow-ups`, fixed here with a test; the redispatch branch lacks that check_cli flag and needs the same change).
- Warm and Codex-b runs include inert injections (wrong_selector) because earlier responders edited the live app; they are excluded from per-fault denominators but included in 'resolved' counts as given.
- Follow-up arm has one silent closure (lane 63) tied to lifecycle-refreshed health detectors, counted as a failure.
- pull-c3-r1 predates the per-fault tracker and is not in the tables.
