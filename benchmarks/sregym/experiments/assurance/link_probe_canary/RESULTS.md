# Link-probe live canary (D29 probe, Codex gpt-6-luna responder)

Branch `vic/exp/link-probe-canary` (off `vic/feat/link-reachability-probe` b9b2079, which is off the phase-1 exec-parity
commit befd946). Run 2026-09-29 02:36-03:03 UTC on `assure-w0` and `assure-w1`, images `sdo-{controller,sregym-responder,detector-validator}:lp1`.
Every SDO agent role is Codex gpt-6-luna (medium); the SREGym judge is codex-gpt-6-luna (xhigh). Judge time is never in TTD/TTM.
Raw per-stage facts: `canary_facts.json` (from `analyze_canary.py`). Configs: `sdo_codex_luna_lp1_canary_{a,b}.toml`, `seed_gen_lp1.toml`.

## Summary

- The probe works live. With a judge-authored `links.yaml`, the link finding fired 5.7-6.1 s after the NetworkPolicy fault in all 6 network-policy
  stages (n=2 lanes x S3, K1, S3 repeat). The responder deleted `deny-all-recommendation` in all 6, and all 8 stages passed diagnosis and mitigation.
  Phase 1 was 0/8 (SDO) and 0/5 (Codex) on both S3 and K1.
- No closure happened while the fault was present: in all 6 policy stages the gate cleared 47-135 s after the link's last active finding, and the
  benchmark mitigation oracle passed. Phase 1 closed 8/8 K1 incidents falsely.
- Getting there needed three fixes in the probe branch's lifecycle path (below). The probe branch as delivered could not complete a lifecycle
  with a judge-authored `links.yaml`.

## Bugs found and fixed on the way to a seed (all on this branch, pushed)

| Commit | Bug | Effect |
|---|---|---|
| `78f69b5` | `ContainerSandboxRunner` defaulted to `sdo-detector-validator:v0.1.0` for the host-side lifecycle validation, ignoring the configured `validator_image`. `v0.1.0` predates the link detector. New env override `SDO_VALIDATOR_IMAGE` (the launcher sets it to `:lp1`). | Seed attempt 1 (`013254`): `traffic.NewLinkDetector` undefined, lifecycle failed. |
| `4505be0`, `c0072e7` | The builder's generated contract tests knew nothing about link probes: `TestRegistrationContracts` expected the 9 s health `MinDuration` that `NewLinkDetector` deliberately leaves unset, and `TestTrafficDetectorsConsumeContinuousProbeWorkloads` accepted only `health-probe` workloads. New unit test `test_check_accepts_a_link_probe_detector_beside_the_health_detector` failed first, passes now. Images rebuilt as `:lp1` (same tag, my own). | Seed attempt 2 (`014955`): both tests failed on every correction attempt. No lifecycle with a judge-written `links.yaml` could ever validate; the no-LLM suite missed it because its seed overlay bypasses the builder's generated tests. |
| not fixed | `lifecycle_seed_stage0` was not written (the checkpoint fix is on `vic/fix/persist-lifecycle-seed` 197705a, not in this lineage). | Seed taken from the stage's `application_workspace` reset to the lifecycle commit `714e5bb` (no incident commits, empty `outcomes.jsonl`). |

Also seen, not fixed: in every one of the 3 seed runs the lifecycle's "session read outside the application repository" guard rejected the
judge's first attempt (a `rg --files | rg ...` and a `cat > ... <<EOF` command), which costs about one attempt (3-4 minutes, ~0.5M tokens) each time.
`v0.1.0` and `:p1` images were not rebuilt or retagged (ids unchanged).

## Seed

`/mnt/data/shli/assure-runs/seed-lp1-714e5bb`, lifecycle commit 714e5bb, from a fresh unseeded stage (`seed_gen_lp1.toml`, lane w1, run `20260929_020808`).

- No `network-policy-total-isolation` anywhere in `.sdo`; `goal.md` has no ConfigMap mention; `outcomes.jsonl` empty; HEAD is the lifecycle commit; the manifest
  watches `ReplicaSet`.
- The judge wrote `workloads/links.yaml` (purpose `link-probe`) and the lifecycle installed `traffic-links` (`NewLinkDetector`). Edges chosen (7, identical to the
  hand-written overlay but authored by the judge here): frontend->search:8082, frontend->recommendation:8085, frontend->profile:8081, frontend->user:8086,
  frontend->reservation:8087, search->geo:8083, search->rate:8084. Ports match the source (`containerPort` 8082/8083/8084/8081/8086 in the deployments; 8085 and 8087
  appear in `config.json`/service specs). No datastores.
- Lifecycle cost of the successful run: 4.03M tokens (one-time, reported separately). The two failed seed attempts were not tallied.

## Per-stage results

Times are seconds after the start of the injection request. TTD is diagnosis POST minus the harness `fault_injected_at`; TTM is judge-free (the
`incident_cost` definition); for K1 the harness anchor is 6.6 s after the request start. "Gate cleared" is the controller's verified-healthy time.
Tokens are responder + reflection (Codex).

| Lane | Stage | Link finding fired | Incident opened | Diag / mitig | TTD | TTM | Gate cleared | Link last active | Tokens |
|---|---|---|---|---|---|---|---|---|---|
| w0 | S3 first | +5.7 | +6.7 | pass / pass | 33.8 | 41.5 | +93.2 | +38.7 | 132,762 + 407,377 |
| w0 | K1 | +5.7 | +0.6 | pass / pass | 33.6 | 59.2 | +148.7 | +60.7 | 304,644 + 250,984 |
| w0 | S1 (regression) | none | +0.9 | pass / pass | 33.5 | 59.1 | +125.5 | - | 306,279 + 354,444 |
| w0 | S3 repeat | +5.9 | +0.6 | pass / pass | 26.9 | 33.1 | +87.4 | +33.4 | 144,767 + 0 |
| w1 | S3 first | +5.8 | +6.8 | pass / pass | 32.3 | 35.7 | +120.6 | +36.3 | 196,641 + 417,551 |
| w1 | K1 | +6.1 | +0.6 | pass / pass | 25.2 | 119.9 | +202.7 | +67.6 | 559,655 + 581,576 |
| w1 | S1 (regression) | none | +0.9 | pass / pass | 27.5 | 61.1 | +125.8 | - | 296,086 + 454,369 |
| w1 | S3 repeat | +6.1 | +0.6 | pass / pass | 33.6 | 46.3 | +108.3 | +42.6 | 183,619 + 0 |

Pipeline totals (responder + reflection): w0 1.90M, w1 2.69M tokens. Codex quota read 90.0% before and after (it does not move at this scale, as in phase 1).

### Answers per item

- **Link finding fired?** Yes in 6/6 network-policy stages, `link-reachability.frontend.recommendation.8085` on `traffic-links`, at +5.7 to +6.1 s (5 failed 1 s dials;
  D29 measured +5.4 to +6.3 s with no LLM). Not in S1 (no link edge broke; D29's no-LLM run also saw `search->geo` there, this run did not).
- **Incident opened?** S3 first: yes, at +6.7 and +6.8 s, one second after the link finding and by the link finding alone (no other detector fired: `first_findings` lists only
  `traffic-links`). K1, S1, S3 repeat: at +0.6 to +0.9 s, from other detectors (the rate ConfigMap health findings, or the learned policy detector, see caveats).
- **Responder and `deny-all-recommendation`?** Found and deleted in all 6 policy stages. S3 first: `kubectl delete networkpolicy deny-all-recommendation` at +38.0 (w0)
  and +35.6 (w1). K1: deleted together with re-applying the rate ConfigMap (w0 +59.6, w1 +67.3). S3 repeat: via the playbook `repair.sh` the earlier stage's reflection wrote.
  The root-cause records name `NetworkPolicy/deny-all-recommendation`, and the S3 diagnosis cites the link finding as evidence.
- **Gate held until the link recovered?** Yes. Example (w0 S3 first, responder's own `sdo incident status` at +17 s): `UNHEALTHY`, with all three user scenarios `ok`
  (error rate 0.00) and the only `FAIL` the `traffic-links` finding. After the delete: `HEALTHY`. The link went inactive within about a second of the delete, and the controller
  verified healthy 47-135 s after the link's last active finding (w0: 54, 88, 54 s; w1: 84, 135, 66 s for S3, K1, S3 repeat).
- **Closure while the fault was present?** None in 8/8 stages. Each closure follows the responder's mitigating command and the SREGym mitigation oracle passed in each.

### Comparison with phase 1 (`PHASE1_RESULTS.md`, same task, same responder model)

| | S3 mitigated | K1 mitigated | Detection of S3 | False closures |
|---|---|---|---|---|
| Phase-1 SDO (8 stages each) | 0/8 | 0/8 | 6/8 no incident in 900 s (censored) | 10, incl. K1 8/8 and 2 S3 flicker stages |
| Phase-1 Codex + verify, exec (5) | 0/5 | 0/5 | told | not applicable |
| Canary SDO, link probe | 4/4 (2 first, 2 repeat) | 2/2 | 2/2 first encounters at +6.7 s, 2/2 repeats | 0 of 8 |

## Caveats

- n=2 per stage (one lane each), one run of one configuration, no baseline arm in this canary. Read it as a mechanism check, not a rate.
- Only the two S3-first stages isolate the probe: nothing else could open an incident there. K1 was preceded by S3 in the same pipeline, so the responder had learned a
  policy-specific incident detector (`recommendation.network-isolation.deny-all` / `deny-all-recommendation-policy`, firing at +0.1 s) and playbook; S3 repeat used that memory
  (5-6 commands, playbook `repair.sh`). K1 and S3 repeat are therefore "probe plus learned memory", and K1's ConfigMap component would have opened the incident regardless.
  What the link finding adds in K1 is a blocking finding that keeps the gate honest.
- The probe's blind spot stays: the fault stayed latent to users (all user scenarios ok during the gate check), and no frontend restart was run here, so a user-visible
  effect was not tested.
- No healthy-soak false-positive measurement on the judge-authored seed. Within these runs no link finding appeared in S1's window (none before injection in S3 first either).
- Both lanes shared one tokenized quota; the launcher's per-lane budget cap and 96% stop were not reached.

## Takeaways

- **Meaning.** The per-edge fresh-dial probe closes the masked-NetworkPolicy gap end to end with a Codex gpt-6-luna responder: a judge-authored links workload turns a fault
  that user probes cannot see into a critical finding in about 6 s, an incident opens, the responder deletes the policy, and the gate refuses to close until the link dials
  succeed again. Phase-1's S3 0/8 and K1 0/8 (with 8/8 false closures) became 4/4 and 2/2 with no false closure.
  **Confidence:** medium-high for the mechanism (6/6 policy stages, two independent lanes, gate output shows only the link finding failing); low for rates (n=2).
- **Implication.** The probe is needed, and it is sufficient for S3/K1 as designed, without any benchmark-specific hint: the judge derived the same 7 edges as the hand-written
  overlay from the source alone. But the probe branch was not deployable through the real lifecycle: two independent bugs (stale validator image on the host path,
  generated contract tests) made every lifecycle with a judge-written `links.yaml` fail. The no-LLM suite could not see them because its seed overlay bypasses both.
  Those fixes should go into the probe branch, and the lifecycle-checkpoint fix (`197705a`) should be merged before another seed run.
- **Next step.** (1) Merge `78f69b5`, `4505be0` (and `197705a`) into the probe branch and re-run the no-LLM suite from a judge-authored seed. (2) Re-run S3 and K1 cold (fresh seed
  per lane, K1 not after S3) with n>=4 and a frontend-restart variant, so the link probe's effect is separated from learned memory. (3) A live healthy soak on the judge-authored
  seed to count link false positives. (4) Decide whether to keep the `SDO_VALIDATOR_IMAGE` override or make the lifecycle validator image an explicit runtime config field.
