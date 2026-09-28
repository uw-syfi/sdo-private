# Phase-1 problem qualification (no LLM, no agent)

Date 2026-09-28. Lane `assure-q0` (kind, 1 control plane + 1 worker, `--cpus 3`),
created by `fastloop up`, hotel reservation deployed from source, and deleted
afterwards. Branch `vic/exp/assurance-phase1`. SREGym `7a510316`
(`vic/feat/composite-problems`). Seed `93f35a93` (lifecycle `30e023d`'s `.sdo` rebuilt by
`assurance seed`). No LLM or Codex call was made.

Raw records are in `qualification/`:
- `qualification.jsonl`: phase-1 oracle qualification, one record per trial;
- `qualification-k3.jsonl`: K3, a phase-2 extra;
- `suite-k1k2.json`: the controller run.

## 1. Oracle qualification (PLAN.md (b), criterion 4)

Command:

```bash
PYTHONPATH=. uv run python benchmarks/sregym/experiments/assurance/qualify.py \
    --run-dir <lane run dir> --trials 3 --out qualification.jsonl
```

Each trial runs these steps:
1. The app is healthy.
2. Inject.
3. The mitigation oracle fails.
4. For a composite only: recover one fault component alone. Fault 0 in trials 1 and 3, fault 1 in trial 2.
5. Recover, which waits for health.
6. The oracle passes.

**Gated checks for composites:**
- After the partial fix, the composite's oracle still fails.
- Every unfixed component's oracle still fails.

Whether the fixed component's own oracle passes is recorded but not gated (see K2).

| ID | Problem | Trials passed | Oracle on fault | After one component fixed: composite / fixed part / other part | Oracle after recovery | Inject s | Recover s |
|---|---|---|---|---|---|---|---|
| S1 | `missing_configmap_hotel_reservation` | **3/3** | fail ×3 | – | pass ×3 | 6.4 | 3.0 |
| S2 | `wrong_service_selector_hotel_reservation` | **3/3** | fail ×3 | – | pass ×3 | 6.1 | 0.2 |
| S3 | `network_policy_block` | **3/3** | fail ×3 | – | pass ×3 | 0.0 | 0.1 |
| K1 | `composite_policy_and_rate_configmap_hotel_reservation` | **3/3** | fail ×3 | fail / **pass** / fail ×3 (both orders) | pass ×3 | 6.5 | 0.0–3.1 |
| K2 | `composite_frontend_selector_and_readiness_hotel_reservation` | **3/3** | fail ×3 | fail / **fail** / fail ×3 (both orders) | pass ×3 | 12.4 | 0.2–2.4 |
| K3 (P2) | `composite_geo_configmap_with_log_drift_hotel_reservation` | **3/3** | fail ×3 | – (one graded fault; the decoy is not graded) | pass ×3 | 8.0–8.8 | 4.7–6.8 |

The recovery column covers only the SREGym call; the health wait comes on top.

Wall clock per trial:

| Problem | Wall clock per trial |
|---|---|
| S1 | 70 s |
| S2, S3 | 10–13 s |
| K1 | 97–209 s |
| K2 | 262–745 s |
| K3 | 73–75 s |

Failing oracles are slow because each one waits out its rollout and probe timeouts,
up to about 120 s + 60 s. K2 has the most failing oracle evaluations.

K3's decoy was checked by hand after the run: `Deployment/geo` env is back to
`JAEGER_SAMPLE_RATIO` only. `benign_env_drift` recorded `LOG_LEVEL` as absent and removed it.

## 2. Controller run: healthy-state diff and the partial-fix gate (PLAN.md (e), C8)

Command: `uv run python -m benchmarks.sregym.fastloop.assurance run --run-dir <lane> --no-single --composite K1 --composite K2 --iterations 3`.

- The composites are injected as the registry problems (`CompositeCase.registry_id`).
- The controller is the production binary, built from the seed's `.sdo` (sha256 `46fb7450ea2b…`).
- The responder is scripted and the broker has no reflector.
- The suite's partial fix recovers fault 0 only.

| Run | Detect s | Request diff (at dispatch) | Live view adds | Union vs `composites.toml` required diff | Unexpected / decoy | After partial fix: status / gate | Full fix: clear s / verify s | Suite checks |
|---|---|---|---|---|---|---|---|---|
| K1 #1 | 1.5 | NetworkPolicy/deny-all-recommendation | ConfigMap/mongo-rate-script | exact | none / none | 1 / 4 (refused) | 30.8 / 48.6 | PASS |
| K1 #2 | 0.6 | both | – | exact | none / none | 1 / 4 | 33.8 / 48.2 | PASS |
| K1 #3 | 0.7 | both | – | exact | none / none | 1 / 4 | 30.1 / 48.3 | PASS |
| K2 #1 | 1.6 | Service/frontend | Deployment/frontend | exact | none / none | 1 / 4 | 3.9 / 23.1 | PASS |
| K2 #2 | 1.6 | Service/frontend | Deployment/frontend | exact | none / none | 1 / 4 | 3.8 / 22.4 | PASS |
| K2 #3 | 1.6 | Service/frontend | Deployment/frontend | exact | none / none | 1 / 4 | 3.9 / 22.9 | PASS |

**Diff vs catalog.** The union of the request diff and the live view names exactly
the catalog's required objects in 6/6 runs, with no unexpected object and no
decoy. K1's optional `Deployment/mongodb-rate` was never named: the injector's
0→1 scale leaves the spec where it was.

**C8 gate.** After a partial fix, `sdo incident status` exited 1 and the
submission gate exited 4 in 6/6 runs (3/3 per composite). After the full fix,
both cleared in 6/6.

**Known gap, reported but not failing.** On K1 the traffic prober never produced a
finding. Detection came from the health detectors. This is the same known gap
as S3: a NetworkPolicy leaves established connections open.

## Takeaways

- **What the data shows.**
  - All five phase-1 problems qualify 3/3: inject, oracle fails, recover, oracle passes. So does the phase-2 K3.
  - Recovering one component of a composite never passes its mitigation oracle (6/6 oracle trials).
  - Recovering one component never lets the SDO gate accept (6/6 controller runs).
  - Composite recovery in reverse catalog order restored a healthy app every time.
- **Confidence.** High for the mechanism: 3 trials per problem, deterministic oracles, and no flake in 18 oracle trials or 6 controller runs. This is one lane and one host, so it says nothing yet about the lane-to-lane variance of the live arms.
- **Implications for SDO and the analysis.**
  - **K2's component oracles are coupled.** The selector's `ServiceEndpointMitigationOracle` and the probe's `ReadinessProbeMitigationOracle` both need frontend to have ready endpoints, so neither passes until both faults are fixed. Per-component "partial mitigation" is therefore not measurable on K2 from the oracles. C8's partial-fix rate on K2 must come from the agents' actions and diffs, not from component oracle verdicts. On K1 the components are independent and the per-component verdicts are meaningful.
  - **The request diff alone misses a composite's later fault.** This is N11 in `NO_LLM_SUITE_DECISIONS.md`. It happened in all 3 K2 runs, because the readiness fault lands about 6 s after the selector fault, and in 1 of 3 K1 runs. The live view covers the gap. But `verify_diagnosis` still checks `state-change` evidence only against the request's diff. On K2 a responder that cites the Deployment/frontend probe change as a state change would get `contradicted`. That is a likely cause of spurious diagnosis-verification failures in the live K2 runs, and it should be fixed before launch (carry the closing view's diff into verification) or at least flagged in the analysis.
  - K1's traffic detector stayed quiet, as the catalog's partial-fix trap predicted. Only the health detectors and the diff reveal the network half of K1.
- **Next actions.**
  1. Fix or flag the N11 verification gap for K2 before the phase-1 launch.
  2. Treat K2 partial-fix counts as action-level in the C8 analysis.
  3. Add K3 to the suite (with the geo drift expected as a decoy object) during the phase-2 prep.
  4. Keep the remaining launch preconditions (PLAN.md): the reset on 2026-10-03, image rebuild, and the smoke row.
