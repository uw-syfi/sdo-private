# Verifier: late detector findings and joined evidence sources

Branch `vic/fix/verifier-late-findings`, from `vic/exp/mixed-integration` (`8ff908ca`), 2026-10-02. Test-first, no cluster run.

## Problem

In the mixed mini stream (phase A step 3, `docs/mixed-stream-decisions.md`) 12 root causes across 7 incidents were `contradicted` although the incidents resolved. Composite faults land a few seconds apart. The health detector dispatches on the first visible fault and the learned detectors activate 7, 14 and 20 s later (a4: `missing-deployment-configmap` +6.7 s, `deny-all-network-policy` +13.9 s, `traffic-links` +20 s). The responder pulled them (pull-before-act) and cited them, correctly. `verify_diagnosis` built its `fired` set only from the dispatch snapshot (`request.findings`, `request.detector_history`), so each citation was `verified=false`, and an explained detector that fired late could never be `flipped`.

Downstream, a contradicted cause is dropped by `controller/runtime/outcome_memory.go` (`discreditedVerdicts`) and reflection learns only from confirmed causes (`reflection.py`), so the bug suppressed learning from exactly the composite incidents. It did not change closure or the outcome class.

A second, smaller defect: a cited source that joins several with `,` or `;` (`missing-deployment-configmap; health-objective; service-endpoints`, `hotel-login, hotel-search, hotel-recommendations`) was compared whole and could never match.

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | The source of late firings is the closure's `detector_timeline` (controller-recorded `DetectorTimelineEntry`, `relation == "after_dispatch"`), passed to `verify_diagnosis` as `detector_timeline`. It reaches the verifier through `OutcomeFacts.detector_timeline`, the broker's `_outcome`, and the SREGym receipt recomputation. | The `late_findings_pull` receipts; the responder's own pull log; extending the Go closure | The timeline already exists in every closure and is written by the controller, so the verifier stays independent of the responder. The pull receipts record what the responder read, which is not a fact about when the controller saw a detector fire. No Go or schema change is needed. |
| 2 | An `after_dispatch` entry counts only if it activated no later than the earlier of `health_cleared_at` and `responder_completed_at` (whichever are known). | Bound by health clearing only; no bound | A finding that appeared after the responder had finished cannot have informed its diagnosis, and one that appeared after health cleared cannot have caused the repair. Both must still be `contradicted`. With neither fact known (an older controller) the entry counts. |
| 3 | `DetectorFlip.fired_after_dispatch: bool = False` is added; `fired_at_dispatch` keeps its meaning; `flipped = (fired at dispatch or after) and cleared`. | Redefine `fired_at_dispatch`; a tri-state | `fired_at_dispatch` stays honest for analysis, and the default keeps old records valid. |
| 4 | A late detector with no post-response evaluation counts as cleared when every late timeline entry for it has a `cleared_at`. A post-response evaluation, when one exists, outranks the timeline. | Extend the Go `incidentDetectorStates` to evaluate late detectors; require an evaluation | The Go controller evaluates only the dispatch snapshot's non-health detectors after the response (`controller.go:1013`), so late learned detectors never get one (a4: `cleared_after_fix` was `None`, which alone would have left the cause `unverified`). The timeline's own `cleared_at` is the controller's record of the clear. This avoids a Go change. A late detector that never cleared stays `unverified`. |
| 5 | Evidence sources are split on `,` and `;` and checked part by part; the check passes only if every part is known. `verified=false` carries a `reason` naming the unreported parts. | Pass if any part is known | Every named source is a claim. A citation that names one real and one never-reported source is partly false. This is also strict for single sources: a never-reported detector is still `contradicted` and now says so. |
| 6 | Late scenario findings are read from the same timeline (`rule_id` starting `scenario-slo.`) and match `synthetic-traffic` evidence. | Dispatch findings only | The same stagger applies to the synthetic-traffic detector. |
| 7 | Mini stream b incident 3's readiness cause stays `contradicted`. | Treat unreported scenario names as unverifiable | Splitting its source (`hotel-login, hotel-search, hotel-recommendations`) shows none of the three scenarios was ever reported as a `scenario-slo.*` finding (its dispatch findings were `health-objective` and `service-endpoints` only). That is a genuine citation of something the controller did not report, not the join defect. The test pins this and the reason names the three parts. |
| 8 | No change to `outcome_memory.go`, `reflection.py`, or the Go side. | Soften the discredit rule | They key on the verdict, which is now correct. Fewer causes will be discredited, so more composite incidents feed reflection and later responders. |

## Not changed, residual risks

- A detector that activated after dispatch and also before the responder began repairs counts the same as one that activated just before completion. The verifier checks that the finding existed while the responder could have read it, not that the responder actually pulled it.
- `health` detectors that fire late (for example `health-objective` for a composite's second fault) are in the late set as well; they were already in the dispatch set when the first fault was the same detector.
- A late detector that cleared before the responder's repair counts as cleared. The attribution rules (F8/F17) are what tie the repair to the cause.
- The ConfigMap cause in a4 also needed attribution to succeed: the verdict is `confirmed` only when the responder's repair backs it; otherwise it is `unattributed` as before.

## Replay

`test_replay_of_the_mixed_mini_stream_a4_composite_confirms_the_late_cited_causes` rebuilds incident 4 from the receipt's own timeline. Before the change both late-cited causes were `contradicted` (the ConfigMap cause because `missing-deployment-configmap` was not in the dispatch set, the NetworkPolicy cause because `deny-all-network-policy` and `traffic-links` were not). After it, no cited detector is unverified and every explained detector flips.

## What a rerun should show

On a composite incident with staggered learned-detector activation, `diagnosis_verification` has no `contradicted` cause for a detector the responder legitimately pulled, the late detectors carry `fired_after_dispatch: true`, and reflection learns from the causes (the playbook and any new detector for the ConfigMap and NetworkPolicy parts). A detector the responder cites that never fired stays `contradicted`.
