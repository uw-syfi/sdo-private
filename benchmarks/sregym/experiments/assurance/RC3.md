# Assurance release candidate 3 (`vic/integrate/assurance-rc3`)

RC3 is the candidate that goes to `main` and then runs the phase-1 live matrix. No LLM or Codex run was launched. The coordinator stopped re-validation early so that experiments could start. This report says exactly what was and was not checked.

## Merged branches

Cut from `vic/integrate/assurance-rc2` `95a075a`, which was still the head at the end (no `RC2.md` had landed yet). Each merge is `--no-ff`, one merge commit per branch.

| # | Branch | Head | Conflicts |
|---|---|---|---|
| 1 | `vic/fix/image-no-benchmark-tree` (F19) | `a5f1d69` | none |
| 2 | `vic/fix/detailor-sdo` (fairness) | `7fcbaa3` | none |
| 3 | `vic/feat/concise-verify-baseline` | `7dc29c9` (contains `140aafb`) | `phase1/RUNBOOK.md` (add/add) |

Resolutions:

- **`phase1/RUNBOOK.md`.** I kept 3's full runbook and made 2's "Seed" section its precondition 0. Its text wins over any mention of `30e023d`. The RC2 references now name RC3.
- **Phase-1 TOMLs.** No textual conflict. The result has 3's arms and lanes (SDO `p1_{a..d}` on w0–w3, Codex concise-verify `p1_{1..4}` on w4–w7, 5 per problem) and 2's `workspace_seed = "PENDING-FRESH-LIFECYCLE-SEED"`.
- **`sdo/agent_runtime/responder/codex.py`, `rbac.yaml`.** These merged cleanly, because 3 does not touch them. The result keeps 2's neutral wording and generic edit Role, the F16 `cancelled` no-action instructions, the F17/F18 state-change evidence text and the `arch.md` inlining.
- **Decision logs.** No duplicate numbers: PLAN D1–D17, CHAOS F1–F19, feedback-loop D1–D28, and `docs/fairness-DECISIONS.md` has its own list.

Integration commits on top:

- `test_benchmark_neutrality.py` now also scans both Codex baseline verify prompts (concise and full). Both were already neutral. The scan previously covered only SDO's sources, so a tailored hint on the baseline side would have gone unseen.
- One pyright error carried by branch 3: `CodexBaselineConfig.__post_init__` narrowed TOML input. Behaviour is unchanged.

The neutrality test passes over the merged tree (15 tests). By hand, I also found no application names (hotel, frontend, geo, recommendation…) anywhere in the scanned SDO roots.

## CI

| Check | Result |
|---|---|
| format (`--check`), ruff, tach, `check_arch.sh` | clean |
| pyright | 0 errors (1 before the fix) |
| `pytest tests/unit`, serial, submodule `7a510316` | ran to completion, exit 0; the pass count was not recorded |
| `go test -race ./...` in sdk, core, runtime | all pass |
| Images under private `:rc3` tags (validator, controller, responder, sregym-responder) + smoke | pass |
| Preflight, both probes, on `:rc3` | ok: codex-cli 0.157.1, agentshim 0.7.0, 3 + 1 images |
| `sdo-sregym-responder:rc3` file listing | only the 12 runtime modules (`benchmarks/{,sregym/}__init__.py`, `sregym/protocol/*` ×7, `sregym/adapter/{__init__,submission,submission_relay}.py`). A whole-image search found no `composites.toml`, `PLAN.md`, `*DECISIONS*`, `catalog.py` or `faults.py` |
| Scripted images `:assure-rc3` (`build_images.sh rc3 assure-rc3`) | built; the scripted CLI probe passes |

The shared `sdo-*:v0.1.0` tags were not rebuilt.

## Re-validation: not run

The coordinator stopped it before any scenario ran. Clusters `assure-t0` and `assure-t1` were created (1+1) and then deleted unused. **Not run:** (a) the scripted scenarios and the chaos regression, (b) coverage without the tailored rule, and (c) the D28 fix-to-healthy check. RC2's results for (a) and (c) came from pre-fairness code and do not cover these merges.

### Found while preparing it: the no-LLM scripted harness cannot use the in-repo seed on RC3

Branch 2 dropped the ConfigMap clause from the adapter's health objective. The objective digest therefore changes (`6931b8e6…` → `f0200bcc…` for hotel-reservation). The scripted harness (`benchmarks.sregym.assurance run`) is reuse-only, and `reuse_initial_lifecycle_if_valid` rejects the `30e023d` seed's recorded health-judge artifact ("objective digest does not match"). **So every scripted scenario on RC3 fails before its first incident** with "the workspace lifecycle is not reusable". The no-LLM suite (`fastloop.assurance run`) compiles the seed's `.sdo` directly and does not do this check. Phase 1 is not affected, because it uses a fresh lifecycle seed.

Until a fresh seed replaces `seeds/hotel_reservation_30e023d`, the fix is a scratch seed variant. It restates the objective, updates the recorded digests and the detector's `healthObjectiveDigest`, and drops the stale validation attestation, so the harness re-runs the no-LLM container validator. I built two such variants, but no run used them:

- `…_rc3objective`: the v3 detectors unchanged, for the mechanics runs.
- `…_rc3generic`: the health-objective detector re-rendered from RC3's v4 template (Deployments available, Services with ready endpoints), for coverage check (b).

They are not committed, because re-attesting LLM provenance in the repo would misrepresent it. The helper is described here only so it can be reproduced. The proper fix is the fresh seed (`vic/exp/fresh-seed`, optional runbook step 6).

## Known gaps

- None of (a), (b) or (c) has been run on RC3. The scripted harness also needs the seed fix above first.
- F1 and the app-internal keep-alive gap (RC1) are unchanged. Once the tailored `network-policy-total-isolation` rule is gone, a fresh-seed SDO may not detect `network_policy_block` and missing-ConfigMap faults from the static detector at all. That is expected under the fairness rules, but it is unmeasured.
- The unit suite's pass count was not recorded.
- `git push` of this branch was denied by the permission classifier during the pass, so the user has to push it.

## Takeaways

1. **The three branches compose without semantic conflict** (confidence high). Static CI, Go `-race`, the images, preflight and F19's image check are green. Implication: RC3 is mergeable as code. Next: merge, then rebuild `v0.1.0` from the merged `main` (runbook precondition 2).
2. **RC3's behaviour is not re-validated at runtime** (confidence: none measured). The merges changed the responder image contents, the responder Role and the lifecycle objective, and all three reach the live path. Implication: the first phase-1 smoke stage is the first runtime check. Watch it for RBAC denials and lifecycle failures. Next: run (a) and (b) on the fresh seed when time allows.
3. **The fairness change breaks the no-LLM scripted harness on the old seed** (confidence high, from reading the code path; not run). Implication: no scripted or chaos regression can run on RC3 or later until the seed is replaced. Next: land the fresh seed and replace `seeds/hotel_reservation_30e023d`.
