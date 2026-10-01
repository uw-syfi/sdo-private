# Pull-before-act live validation: decisions and results

Branch `vic/exp/late-fire-integration` = spec-first + detector-firing telemetry + `vic/feat/late-findings-pull` (merge a099df8). Images `lf2` (controller, responder, sdo-sregym-responder, detector-validator) built with `SDO_IMAGE_TAG=lf2 scripts/build_sdo_images.sh` from commit 2dda07a. `lf1` and other tags untouched. Note: lf2 does not contain the receipt-path fix (D7, commit 12ddda4).

## D1 Merge
- What: `git merge --no-ff origin/vic/feat/late-findings-pull`. One conflict in `benchmarks/sregym/fastloop/cli.py` (both sides edited the `--reflection-guidance` choices line); resolved by keeping `generalize-spec` and adding `--late-findings`.
- Why: the two features are independent; the union is the only coherent result.
- Post-merge checks: format, `check_errors` (ruff, tach) clean; Go sdk/core/runtime ok; `tests/unit` 1850 passed. Two failures: a flaky crucible property test (passes on rerun) and `test_cross_package_imports_go_through_facades`. The latter had 3 violations: two were from the pull branch (`late_findings` imported past the `operational_memory` facade; fixed by exporting `LATE_FINDINGS_COMMAND` and `late_findings_main`, commit 2dda07a), one pre-existing in `benchmarks/sregym/analysis/reflection_replay.py` (also fails on a1ac61b; left alone).

## D2 Scenario
- Seed: private copy of `/mnt/data/shli/detgen-runs/seeds/lifecycle-stream` plus the learned `frontend-readiness-probe-mismatch` incident detector, playbook (with `repair.sh` / `verify.sh`) and manifest entry taken verbatim from `detgen-runs/replay1/clone-baseline`. Its `Detect` requires an `Unhealthy ... :8080 ... connection refused` Kubernetes Event on the frontend pod, so it cannot fire until the kubelet has reported the failing probe. Compiled and its unit test passed against the current SDK before launch. Both arms got byte-identical seeds (`/mnt/data/shli/lfint-runs/seeds/pull-{on,off}`), committed in their own git repos.
- Incidents: A1 (`readiness_probe_misconfiguration_hotel_reservation`) three times. Alternatives: A1, A2, A3 variants. Why not: the seeded detector is frontend-specific, so only A1 exercises it. Reflection was kept on (cannot be disabled in this config surface, `reflection_guidance = "baseline"`, `reflection_session = "fresh"`). Consequence: reflection after incident 1 rewrote the detector (see results), so only incident 1 is a true late-firing test; incidents 2 and 3 act as controls with the detector firing before dispatch.
- Arms: `sdo_codex_luna_pullval_{on,off}.toml`, identical except `late_findings = "pull"` vs `"off"`. Codex gpt-6-luna for agent and judge (xhigh judge), 1+1 kind clusters `lf-w32` (ON, launched 22:10:58) and `lf-w33` (OFF, launched 22:11:27). Load at launch 8.2-8.8, so no further staggering. Clusters deleted afterwards.

## D3 Results (pipelines `20260930_221058_pipeline_sdo-codex-luna-pullval-on`, `20260930_221127_pipeline_sdo-codex-luna-pullval-off`, under `third_party/sregym/logs`; tables in `/mnt/data/shli/lfint-runs/table_{on,off}.json`)

All 6 incidents: strict receipt, diagnosis True, mitigation True (3/3 solved per arm). TTD/TTM are the harness values, judge time excluded (deferred grading). "Fire" = incident detector `activated` minus dispatch time from the closure `detector_timeline`. Tokens are responder in/out (reflection excluded).

| Inc | Arm | Fire vs dispatch | Pulls (empty / non-empty) | TTD s | TTM s | Solved | Responder tok in/out |
|---|---|---|---|---|---|---|---|
| 1 | ON | +12.37 s (after) | 2 (1 / 1) | 43.8 | 93.3 | yes | 504k / 4.8k |
| 1 | OFF | +12.31 s (after) | n/a | 39.1 | 67.0 | yes | 382k / 3.4k |
| 2 | ON | -0.49 s (before) | 2 (2 / 0) | 53.8 | 58.3 | yes | 233k / 2.5k |
| 2 | OFF | -0.49 s (before) | n/a | 31.6 | 65.8 | yes | 338k / 3.5k |
| 3 | ON | -0.54 s (before) | 2 (2 / 0) | 41.9 | 46.5 | yes | 156k / 2.0k |
| 3 | OFF | -0.48 s (before) | n/a | 30.0 | 71.0 | yes | 307k / 3.5k |

Late firing is reproduced on incident 1 in both arms (+12.3 s, matching the earlier 11-12 s figure). The ON and OFF requests carried only `health-objective` findings at dispatch.

Incident 1, what the responder did:
- ON: pull 1 at +10.3 s after dispatch (22:19:44) returned `late_findings: []` (detector fires at 22:19:46.2). The responder diagnosed from live spec and source (probe 8080 vs listener 5000), submitted the diagnosis at 22:20:00 (the finding had existed for 14 s) without re-pulling, then pulled again before its first change at 22:20:04. That pull returned the late finding with the playbook `frontend-readiness-probe-mismatch`. It read the playbook and its scripts, ran `repair.sh` and `verify.sh`, and reported both the health playbook and the late playbook as applied. The diagnosis was not changed by the late finding (it was already right and already submitted); the mitigation path was changed (playbook script instead of an ad hoc patch) and was about 26 s slower in TTM (93 vs 67 s), including the extra playbook reading and verification.
- OFF: never saw the learned playbook in its request. It diagnosed from pod Events and live spec, patched the probe directly, and applied only the health playbook. Correct diagnosis and mitigation, faster.
- Incidents 2 and 3 (ON): both pulls empty, the detector was already in the request, no behavior difference attributable to pull. Their token and TTM differences are not attributable to the flag (see caveats).

## D4 Takeaways
- Meaning: the pull channel works end to end in a real cluster. The responder followed the guidance, pulled twice, the first pull was empty because it ran only 10 s after dispatch, and the second (before the first change) surfaced the late Event-based detector and its playbook, which it then applied. Scoping was correct (other incidents, before-dispatch and health findings were never returned).
- Confidence: high that the mechanism works (raw pull receipts and Codex transcripts agree); low on any effect on quality or speed (n=1 late-firing incident per arm).
- Implication: on this scenario pull-before-act did not change the diagnosis and cost time, because the model finds the A1 cause from live state in under 45 s, before the Event-based detector can fire (12 s) and before the responder's second pull. Its value is for incidents where the responder has not yet committed to a diagnosis when the late finding arrives, or where the playbook carries a non-obvious repair. The wording "run once now and again before the first change" puts the second pull after diagnosis for fast models.
- Next step: (a) rebuild images with the D7 fix and rerun so the closure/receipt fields are correct; (b) ask for a pull before the diagnosis is submitted (or shortly after dispatch-plus-expected-latency) and compare; (c) use a fault where the live cause is ambiguous and the learned playbook disambiguates, with several repeats per arm.

## D5 Caveats
- n=1 late-firing incident per arm; TTD/TTM differences of 10-30 s are within run-to-run variation here and are not a claim about the flag.
- Reflection rewrote the detector after incident 1 in both arms (`reads_events: false` afterwards, spec-based), so incidents 2-3 no longer fire late. They are controls for false "pull" behavior, not late-firing tests.
- The seeded playbook lives in the repository in both arms, so OFF could in principle discover it; it did not (it read only `health-objective`).
- ON tokens are not comparable across arms beyond incident 1: the ON arm had already learned a different playbook text by incidents 2 and 3.
- Hand-seeded detector from an earlier run, not produced in this stream.

## D6 Output noise
`python3 -m sdo.operational_memory.late_findings` prints a runpy `RuntimeWarning` to stderr because the package `__init__` imports the module. It does not affect behavior or JSON stdout. Not fixed here.

## D7 Bug found: pull receipts were not folded into closures
- Symptom: the strict receipts of the ON arm report `late_findings_pull: {pull_count: 0, pulled: false}` and `late_finding_consumed: false` for all three incidents, although `late-findings-pulls.jsonl` and the transcripts show 2 pulls per incident (one non-empty on incident 1).
- Cause: the broker looked for the receipt file under its target repository (`/workspace/application/.sdo-runtime/telemetry`), while the responder appends it at the volume root (`/workspace/.sdo-runtime/telemetry`), next to the firing stream.
- Fix (test first, commit 12ddda4): `BrokerService(late_findings_log=...)`, broker CLI `--late-findings-log`, and the controller install passes `/workspace/.sdo-runtime/telemetry/late-findings-pulls.jsonl`. Unit tests cover both. The lf2 images predate the fix, so the pull evidence in this report was taken from the raw pull receipts (`sdo_runtime/telemetry/late-findings-pulls.jsonl`) and the responder Codex session transcripts, not from the closure fields.
- Note: the receipt file is cumulative over the persistent stream; it is filtered per incident id, which is correct.
