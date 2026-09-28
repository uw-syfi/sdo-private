# Assurance release candidate 1 (`vic/integrate/assurance-rc1`)

RC1 integrates the 2026-09-28 assurance branches into one branch as the release candidate for the live phase-1 matrix after 2026-10-03. No LLM or Codex run was launched. Every decision below was made autonomously.

## Merged branches

The branch was cut from `origin/main` `8cadce1`. Merges are `--no-ff`, one merge commit per branch, in dependency order.

| # | Branch | Head merged | Merge commit | Conflicts |
|---|---|---|---|---|
| 1 | `vic/exp/feedback-loop-e2e` (PR #7, base) | `104162b` | `9fcc7ae` | none |
| 2 | `vic/feat/harness-assurance` | `4b6a2b5` | `0910f18` | none |
| 3 | `vic/feat/no-llm-assurance-suite` | `b1ba89c` (re-checked at the end: unchanged) | `3dec4e4` | none |
| 4 | `vic/feat/scripted-responder-chaos` | `f2018b5` | `f59484c` | `docs/architecture.md` |
| 5 | `vic/fix/controller-retry-residuals` | `83a8732` | `2da71b7` | none |
| 6 | `vic/fix/prober-fresh-connections` | `276d574` | `209463f` | none |
| 7 | `vic/perf/responder-context-audit` | `5341353` | `db726fc` | none |
| 8 | `vic/exp/assurance-phase1` | `70cddbb` | `56bd001` | `fastloop/assurance/catalog.py`, `suite.py` |
| 9 | `vic/fix/late-fault-diff` | `341a7c5` | `bb84a07` | `protocol.go`, `state_store.go`, `trajectory-schema.md`, D-number |
| 10 | `vic/fix/traffic-min-duration` | `1f24034` | `861989c` | `docs/feedback-loop-DECISIONS.md` |

Integration fixes on top:

| Commit | What |
|---|---|
| `78422ab` | Four pyright errors carried by branches 3 and 4, which never ran the type-check job |
| `e8784f2` | Decision-log notes: N11 and N13 addressed by D26/D27; F8's interaction with N11 |
| `22f55c0` | `tests/unit/test_architecture.py`: the scripted harness imported past the lifecycle facade |
| `950a4b7` | **Controller bug found by re-validation:** an empty closing-view diff was persisted as `changes: null` (below) |
| this file | RC1 report |

### SREGym submodule

Every bump is on one linear fork history: `vic/feat/kind-worker-nodes` `5913e008` < main's `2933dbe4` < harness `cbb9715f` (Codex CLI pin) < `vic/feat/fastloop-composite-faults` `a1c00a3e`, `3018dbc2` < `vic/feat/composite-problems` `bc7d51f4`, `7a3cd727` < `7a510316`. The one commit that holds every needed change is therefore `7a510316`, the phase-1 bump. No submodule merge was needed. `7a510316` existed only in worktree `agent-a8ec53f5004db8ea5`'s submodule; I fetched it from there and pushed it to the fork as a fast-forward of `vic/feat/composite-problems` (`7a3cd727..7a510316`). The superproject points at it.

## Conflicts and resolutions

- **`docs/architecture.md` (4 against 3).** The responder paragraph changed on both sides. Kept 4's prober Service address and detector-review text, and added 3's `incident_view` / `SDO_CONTROLLER_STATE` sentence.
- **Controller dispatch and closure (F9–F15 against the feedback loop).** `controller.go` and `run.go` merged without textual conflicts. I reviewed the combined diff. F9's terminal `ResponderJobFailedError`, F13's `detectorOrigins`, F4/F6's `detectorReviewGate`, F11's `executePausedEffects` and F10's `responderProberAddress` all sit beside 3's `incident_view` refresh and `SDO_CONTROLLER_STATE` wiring, and none of them changes what the others read. F14 (dispatch retry backoff) and F15 (reflection attempt counting) merged cleanly on top. `go test -race` passes in all three modules.
- **`IncidentClosure` and `cloneIncidentClosure` (9 against 4).** F4 added `DetectorReviewRequiredAt`/`DetectorReviewReason` and N11 added `FinalStateChanges` at the same lines. I kept both fields and both deep copies.
- **`verify_diagnosis` (N11 against F8).** N11 merged as written: state-change evidence is checked against the union of the dispatch-time and closing-view diffs. **Interaction:** the closing view is the diff against the healthy baseline at verification time, so it also contains changes the responder made and did not undo (a rollout restart's `restartedAt`, which N12 showed the diff reports). A wrong fix can therefore cite its own edit as a `state-change` and get `confirmed`. That makes a state-change citation, which F8 proposed as a possible attribution signal, weaker. In the other direction, a late fault that the responder reverted exactly is gone from the closing view, so N11 only helps when the late component still differs at verification time. I noted this under F8 in `CHAOS_DECISIONS.md` and did not change the code: the fix is attribution (F17 on `vic/fix/repair-attribution`, which is for rc2).
- **`controller/sdk/traffic` (6 against 10).** It merged cleanly. 6 labels dial-phase failures in `ScenarioFinding` and the engine. 10 defaults `Persistence.MinDuration` to 9 s in `NewDetector` for health-class specs. They touch different functions and compose: a stall classified as a dial timeout still has to persist for 9 s before it fires.
- **Decision logs.** In `docs/feedback-loop-DECISIONS.md`, both 6 and 9 used D25. 6 merged first and keeps D25. **9's "N11 fix" is renumbered D26**, with a note under the heading. 10's D27 keeps its number, and D26/D27 now appear in order. No F- or N-number collided (F1–F15 come from branches 4 and 5, N1–N13 from branch 3).
- **`fastloop/` (8 against 3).** Both branches added the K1/K2 registry composites. 3's version (`84c8702`) is richer, with decoys, wrong fixes and K3. 8's is referenced by `composites.toml`, the qualification records and its tests under the plan's short ids `K1`/`K2`. I kept 3's three cases under the ids `K1`, `K2`, `K3`, plus 8's `registry_id` prefix check and `inject_case`, and updated 3's test that looked up `K3-geo-configmap+log-drift`. `QUALIFICATION.md` already used `--composite K1`.
- **`incident_cost` (2 against 7).** No textual conflict: 7 is a read-only audit that applied 2's validity criteria by hand because `run_validity` was not on its branch. With both merged, `python -m benchmarks.sregym.analysis.run_validity --legacy` over the audit's runs classifies all four SDO `network_policy_block` pipelines as `valid`. The Codex baseline gets 1 `valid` and 2 `agent_failure`, with none `invalid_infra`. That matches the audit's hand check. `incident_cost --legacy-runs` over `081908` runs and reports 171,673 responder tokens (29,983 uncached, 139,520 cache read), consistent with the audit.

## CI and tests (integrated head)

| Check | Result |
|---|---|
| `scripts/format_code.sh` (and `--check`) | clean, 328 files |
| `scripts/check_errors.sh` (ruff, tach) | clean after `22f55c0` |
| `scripts/type_check.sh` (pyright) | 0 errors after `78422ab` (4 before) |
| `scripts/check_arch.sh` | clean |
| `uv run pytest tests/unit`, submodule at `7a510316` | **2017 passed, 6 skipped** (serial, as CI; `-n` xdist collection differs between workers on set-ordered parametrizations) |
| `go test -race ./...` in `controller/sdk`, `core`, `runtime` | all pass |
| CI image job: build + smoke + preflight | pass: codex-cli 0.157.1 everywhere, agentshim 0.7.0, 3 + 1 images probed |

The CI image job tags the shared `sdo-*:v0.1.0` images, and other lanes run from those tags. I ran the same build steps (`scripts/build_sdo_images.sh`) under private `:rc1` tags instead, plus both preflight probes. The scripted images are `build_images.sh rc1 assure-rc1`, which is the `checkout` mode of that script with the validator also taken from this checkout.

## Re-validation

Lanes: my own 1+1 kind clusters `assure-i0` and `assure-i1` (3 CPUs per node), created with `fastloop up`. Images were built from the RC1 checkout: `:rc1` for the no-LLM suite and qualification, `:assure-rc1` for the scripted harness. SREGym is at `7a510316`. The no-LLM suite's controller and prober binaries are compiled from the checkout by the production builder.

### Controller bug found: an empty closing-view diff wedged the persistent controller

The first scripted `first-repeat` run on `assure-i1` failed. The first incident was repaired, and then the closure failed permanently after 8 broker attempts with `BrokerClosure final_state_changes.changes: Input should be a valid list (input None)`. N11's `cloneStateChanges` appended an empty `Changes` slice to `nil`, so a clean closing view, the normal case after a correct fix, was persisted as `"changes": null`. A permanently failed closure blocks every later incident in a persistent controller, so **a live phase-1 SDO arm on branch 9 would have stopped after its first correct repair.** Fixed test-first in `950a4b7`: the new test fails with `got null` before the fix. The same helper also clones the live `incident_view`, so the fix covers it too. The images were rebuilt and every result below comes from the fixed build. The earlier run is kept in `logs/i1-first-repeat.prefix.log`.

### Results

| Check | Lane | Before (branch runs, 2026-09-28) | RC1 |
|---|---|---|---|
| Qualification `qualify.py`, S1 S2 S3 K1 K2, 1 trial | i0 | 5/5 ×3 (`QUALIFICATION.md`) | **5/5** |
| No-LLM suite, 6 single faults (hold+re-inject on #1) | i0 | 6/6 (`singles2`) | **6/6** |
| No-LLM suite, 6 composites (3 compositions, K1–K3) | i0 | K1/K2 3/3 (`suite-k1k2`), compositions 3/3 once N11 accepted the live view | **6/6** |
| Stray incidents over the suite | i0 | 1 per 5-iteration pass (N12) | **0** in 12 cases |
| Scripted `first-repeat` (cold, then warm) | i1 | pass after F13 | **PASS / PASS** (repeat took the warm path) |
| Scripted `wrong-then-correct` | i1 | pass | **PASS** |
| Scripted `wrong-only` (honest, claimed) | i1 | pass | **PASS / PASS** (neither cause learned) |
| Scripted `sequence` (5 incidents) | i1 | pass | **5/5 PASS** (np, geo, np-repeat warm, rate-variant, geo-repeat warm) |
| Scripted chaos regression (extra; F9–F15 on the merged controller) | i1 | pass (`c0-regression`, 4 scenarios) | **8/8 PASS**: kill-responder, reflection-crash-always, kill-prober, broker-reject, kill-controller, reflection-crash-once, concurrent-commit, pause-apiserver |
| `churn1` soak (N13) + dense churn | i0 | 1 stray dispatch, 7/377 evaluations with findings | 0 dispatches, 0/345 and 0/497 with findings; see the stray-dispatch section |

No-LLM suite latencies (seconds; `detect` from the start of injection, `clear` = correct fix → `incident status` healthy, `verify` = fix → controller verified):

| Case | detect | traffic | diff | wrong fix | partial fix | clear | verify |
|---|---|---|---|---|---|---|---|
| selector-mismatch | 2.6 | 4.5 | exact | refused/refused | - | 31.4 | 44.4 |
| missing-configmap | 0.7 | - | exact | refused/refused | - | 34.2 | 45.5 |
| network-policy-block | 1.5 | - | exact | refused/refused | - | 36.2 | 55.0 |
| readiness-probe | 0.8 | 3.7 | exact | refused/refused | - | 31.7 | 44.6 |
| missing-service | 0.7 | 3.1 | exact | refused/refused | - | 4.1 | 20.3 |
| missing-configmap-rate | 0.7 | - | exact | refused/refused | - | 36.2 | 55.9 |
| policy-block+selector | 0.7 | 3.9 | exact | - | refused | 31.9 | 44.8 |
| configmap-geo+selector | 0.8 | 10.0 | exact+live(Service/frontend) | - | refused | 31.7 | 44.8 |
| configmap-geo+configmap-rate | 0.7 | - | exact+live(ConfigMap/mongo-rate-script) | - | refused | 31.6 | 41.9 |
| K1 | 0.7 | - | exact | refused | refused | 30.5 | 43.4 |
| K2 | 1.6 | 3.6 | exact+live(Deployment/frontend) | refused | refused | 31.2 | 43.8 |
| K3 (decoy) | 0.7 | - | exact | refused/refused | - | 30.2 | 42.1 |

The pre-D28 baseline the coordinator asked for (fix → healthy status / fix → closure): selector 31.4 / 44.4 s, missing-configmap 34.2 / 45.5 s, network-policy 36.2 / 55.0 s, and composite K1 30.5 / 43.4 s (K2 31.2 / 43.8 s). rc2 should compare D28 against these.

Readiness-probe's clear went from 3.9 s in `singles2` to 31.7 s. With one sample each, this is `health-objective`'s 30 s cadence landing on either side of the recovery: N10 already reports that `incident status` waits for the next non-traffic evaluation. It is not a regression signal. The traffic column is the traffic prober's first raw finding. Incidents opened on the static detectors first, so the 9 s min-duration did not delay any `detect` value here. `network_policy_block`'s traffic column is still `-` (F1).

### Stray dispatches before and after the 9 s min-duration (D27)

| Run | Churn cycles inside the soak (each one creates and deletes a pod or Job) | Evaluations with a traffic finding | Traffic-finding episodes | Incidents opened |
|---|---|---|---|---|
| `churn1` before (assure-s1, branch 3, no min-duration) | 6 | 7 / 377 | 1 (about 3 s, all three scenarios) | **1** (dispatched 7 s after a Job pod was created) |
| `rc1-churn` (same script and cadence) | 5 (the first of 6 fell before the soak window opened) | 0 / 345 | 0 | **0** |
| `rc1-churn-dense` (one create/delete a minute, alternating pod/Job and namespaces) | 26 | 0 / 497 | 0 | **0** |

The healthy soaks' other checks passed. `incident status` was healthy in 6 of 6 probes. The post-soak selector fault was detected with an exact diff, and controller CPU stayed at 12–16 millicores (10 before).

**What this measures.** Zero stray dispatches in 31 churn cycles, against 1 in 6 before. **But the stall that caused N13's strays never happened in RC1:** there were no raw traffic findings at all, so the 9 s `MinDuration` never had a violation to suppress. At N13's rate (about 1 stall per 6–10 pod-network changes, and each cycle makes two) several stalls were expected across 31 cycles. Seeing none is unlikely by chance, so the trigger's rate changed between the two runs. I did not isolate why. Candidates: host load, since the earlier runs shared the host with more concurrent lanes, and the prober's D25 dial changes, which the `churn1` build lacked. So RC1 shows that the min-duration rule does not delay real detection (every `detect` value above is unchanged, because the static detectors open the incidents), and that no stray reached dispatch. It does **not** show live suppression of a real 3 s stall. That evidence is still only D27's unit and runtime tests. Next step: reproduce a stall on purpose (for example a `tc netem` delay on the kind worker's veth for 3 s) and confirm that a 3 s violation does not open an incident while a 10 s one does.

## Remaining known gaps

- **F1:** the verify burst and the traffic prober miss a fresh deny-all NetworkPolicy. Only `health-objective` catches it, and the suite's traffic column is still `-` for every NetworkPolicy case in RC1 (network-policy-block, policy-block+selector, K1). N10's `incident_view` keeps `incident status` and the submission gate correct regardless, because they now defer to the firing static detector.
- **App-internal keep-alive gap (D25):** the prober already dials fresh for every request. The masked hop is inside the application: `frontend` keeps its established connection to `recommendation`, which the NetworkPolicy does not tear down, and keeps answering 200 until it redials. No prober-side change can close this. Detecting such faults rests on the static detectors, or on a probe that targets the internal hop directly, which is a health-judge question.
- **F8, attribution:** a wrong claimed cause is `confirmed` if someone else fixes the fault inside the verification window. N11 (above) widens the evidence a wrong fix can cite. Fixing it is F17's job (`vic/fix/repair-attribution`, rc2).
- **Disk-full chaos:** not run. The local-path PVC is not size-limited and would fill the shared `/mnt/data`.
- **Residuals already logged and still open:** any other permanent closure failure blocks later incidents until an operator acts (loud, not self-healing). The N13 sharp edge, where a responder answers a healed stray `completed` with no action, is F16 (`vic/fix/noop-closure-cancelled`, rc2).
- **N11 null bug:** fixed here (`950a4b7`). The branch it came from (`vic/fix/late-fault-diff`) still has it.

## Decisions (autonomous)

- **Private image tags.** The shared `v0.1.0` tags were not rebuilt, because other agents' lanes run from them. The phase-1 configs still name `sdo-*:v0.1.0`. **Before the live matrix, rebuild `v0.1.0` from RC1** (`scripts/build_sdo_images.sh` at the RC1 head, while no lane uses the tags) and re-run the two preflight probes. Otherwise the SDO arm runs without `950a4b7` and wedges after its first correct repair. I did not repoint the configs to `:rc1`, because every luna config and the Codex arm share the `v0.1.0` convention.
- **K ids.** The composite cases are named `K1`–`K3`, the plan's ids, rather than the suite's descriptive names.
- **D-number.** The prober fix keeps D25 and N11 becomes D26 (merge order).
- **N11 kept as written despite the F8 interaction.** Changing verification semantics is F17's scope. Dropping N11 would bring back the `contradicted` verdicts on composites.
- **Scripted runs one scenario per `--fresh` run.** `sequence` needs cold memory. The harness's default `--verification-timeout` is 1200 s, whereas the earlier branch runs passed 420 s. That is why each `wrong-only` incident took about 21 min here against about 8 min before. The outcome is the same.
- **A killed scripted run leaves its fault behind** (already logged in CHAOS_DECISIONS). After I stopped the null-bug run, `deny-all-recommendation` stayed in place and the next `--fresh` run refused. I deleted it by hand.
- **SREGym venv.** Two concurrent `fastloop up` runs raced creating the submodule's `.venv` (greenlet copy failure). A single `uv sync` in `third_party/sregym` first avoids it.
- **Push.** `git push -u origin vic/integrate/assurance-rc1` was denied by the permission classifier. Per the rules I did not retry it. The SREGym fork push succeeded.
- **rc2.** Three more branches arrived while this pass was finishing: `vic/fix/noop-closure-cancelled` (F16), `vic/perf/status-clear-latency` (D28) and `vic/fix/repair-attribution` (F17). They are not in RC1. The coordinator will dispatch rc2 separately.

## Takeaways

1. **RC1 is a sound integration and can serve as the phase-1 baseline, once `v0.1.0` is rebuilt from it.** The branches compose. Every conflict was additive: both sides were kept, and none needed a semantic choice beyond K naming and D-numbers. CI is green (2017 unit tests; Go with `-race`; ruff, tach, pyright; the image job and preflight). All 12 no-LLM cases, all 4 scripted scenarios (10 incidents), the 8 chaos scenarios and 5/5 qualification pass on images built from the checkout. Confidence: high for the paths covered, and every run is a single sample. Implication: the live matrix can start from RC1's code. Next: rebuild the shared tags from RC1 before 2026-10-03 and re-run preflight.
2. **Integration re-validation caught a wedge that no branch caught on its own.** N11's closing-view diff serialized an empty change list as `null`, the broker rejected every closure after a correct repair, and the persistent controller would have stopped detecting after its first success. Unit tests on both sides passed, because each side built its values in its own language. Confidence: high (reproduced, then fixed test-first). Implication: any Go→Python contract field needs a cross-language round-trip test. Next: add a contract test that decodes Go-encoded closures with the Python `BrokerClosure` for every optional field.
3. **The suite's latencies are unchanged by integration.** Detection takes 0.7–2.6 s, the diffs are exact, or exact plus the live view on the late-landing composites, and every wrong or partial fix is refused. Fix → healthy status is 30–36 s wherever a static health detector fires (N10's cost), and fix → closure is 42–56 s. These are the pre-D28 numbers that rc2's status-clear change should cut to about 4 s.
4. **Stray dispatches: 0 in 31 cycles after, 1 in 6 before, but the underlying stall did not recur.** RC1 cannot credit the 9 s min-duration with the drop. Confidence that the rule is harmless to detection: high. Confidence that it suppresses real stalls: moderate, from tests only. Next: an injected-stall test.
5. **Attribution is the largest open correctness gap.** N11 made state-change citations easier to confirm, and a wrong fix's own edits are in the closing view. F8 plus N11 means a wrong claimed cause can be learned if the fault clears by other means within the window. F17 (rc2) is the fix, and it should be validated with `wrong_only_claimed` plus an operator fix inside the window.
