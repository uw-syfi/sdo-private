# Responder: evidence-kind discipline

Branch `vic/fix/responder-evidence-kind`, from `vic/exp/mixed-integration` (`0775ab18`), 2026-10-02. Test-first, no cluster run.

## Problem

In the 10-incident mixed pilot (mi6, luna xhigh judge; `docs/mixed-stream-decisions.md`) the two composites verified cleanly (3/3 confirmed) after the late-findings verifier fix. Four *single*-fault incidents still carried one `contradicted` cause each, although each incident was mitigated with an attributed repair:

- i02 / i04 (`wrong_service_selector`, `readiness_probe_misconfiguration`): `synthetic-traffic` source `hotel-search; hotel-recommendations; seeded-user-login; synthetic-reservation` — three real failing scenarios plus `synthetic-reservation`, which is not a probed scenario.
- i03 (`missing_configmap` variant): `synthetic-traffic` source `sdo-incident-status` — SDO's own verify command, not a scenario.
- i09 (`misconfig` exact): `detector-finding` source `change-diff` — the configuration diff, not a detector.

The responder cited a real operational signal under the wrong evidence kind (or named a non-existent scenario). `verify_diagnosis` correctly found that source was never reported as a detector/scenario finding and marked the item `verified=false`, which makes the whole cause `contradicted`. A contradicted cause is dropped by `controller/runtime/outcome_memory.go` and reflection never learns from it — so an otherwise sound, solved cause produced no playbook or detector. The `live-observation` kind already exists for exactly these ad-hoc checks (it is `verified=null`, accepted-but-unverified, and never contradicts); the model simply used the wrong kind.

This is a different class from the late-findings fix. It is not blocked on resolution — the incidents were solved. It is a verification-quality / learning concern only.

## Decisions

| # | Decision | Alternatives | Why |
| --- | --- | --- | --- |
| 1 | Prompt guidance (primary): `_verification_instructions()` now tells the responder to match the kind to what it observed — detector-finding only for a reported detector ID, synthetic-traffic only for a failing scenario ID — and that `sdo incident status`, a `kubectl`/`curl`, and the configuration diff as a whole are live observations, a listed change is a state-change. It also states that SDO degrades an unrecognized detector/scenario source to a live observation. | Prompt only | The prompt reduces the mistake at the source; the deterministic degrade (decision 2) guarantees a sound cause is still learnable when the model slips. |
| 2 | Deterministic backstop: `_normalize_evidence_kinds` runs on the parsed `IncidentResult` in `execute_incident`, before the result is returned. For a `detector-finding` or `synthetic-traffic` item it splits the source (same `,`/`;` split as the verifier) and checks each part against this incident's catalog — detectors `{findings ∪ detector_history}.detector_id`, scenarios the `scenario-slo.`-stripped `findings[*].rule_id`. Parts the catalog recognizes stay cited; unrecognized parts become a `live-observation` item (or the whole item is relabeled when none are recognized). | Degrade at the verifier; hard-reject the receipt; whole-item degrade on any unknown part | Degrading at the responder keeps the verifier's contradiction semantics (an SDO invariant that gates reflection) untouched. Hard-rejecting would fail a well-repaired incident over an evidence-kind nit. Per-part keeps the three real scenarios in i02/i04 as synthetic-traffic and moves only `synthetic-reservation`. |
| 3 | Catalog membership only, never a fault- or scenario-specific name, so the rule is generic evidence hygiene (satisfies `docs/fairness-DECISIONS.md`). | Recognize known non-evidence tokens (`change-diff`, `sdo-incident-status`) | Hard-coding tokens is fragile and benchmark-coupled. Membership generalizes to any application's detectors and scenarios. |
| 4 | A source that *is* in the catalog but did not fire is left untouched. | Degrade any source not in the firing set | Red-herring preservation: a cause that cites a real detector that never produced a finding is a genuine false citation and must stay `contradicted`. The degrade only rescues sources the controller does not know at all. `test_known_detector_that_did_not_fire_is_not_degraded` pins this. |
| 5 | The splitter and scenario prefix are kept local to `codex.py` with a comment pointing at the verifier, not shared. | Promote `diagnosis._source_parts` / `SCENARIO_RULE_PREFIX` | The sibling branch `vic/fix/verifier-late-findings` rewrites `diagnosis.py`; editing it here would conflict. The regex and prefix are stable and covered by `test_normalized_cause_is_no_longer_contradicted`, which runs a degraded result back through `verify_diagnosis`. |
| 6 | No verifier, `outcome_memory.go`, reflection, schema, or analyze-experiment-reference change. | — | No evidence format changed; the `kind` Literal and JSON-schema enum already include `live-observation`. The fix only chooses a different existing kind. |

## Late-findings interaction (known, verdict-safe)

`execute_incident` sees only the dispatch-time request, not the controller's future `detector_timeline`. A detector or scenario the responder pulled after dispatch (pull-before-act, the composite path) is not in the dispatch catalog, so decision 2 relabels its citation to a `live-observation`. This does **not** change the cause's verdict: confirmation flows through `explained_detectors` flips, which the verifier credits from the late `detector_timeline` regardless of the evidence item's kind, and repair attribution (F8/F17) is the real guard against a fabricated cause. The only effect is that such a citation is recorded as a live observation rather than a detector-finding. The composite pilot causes therefore still confirm (3/3). Genuine fabrication of a wrong cause is still refused by attribution, not by evidence contradiction, which is unchanged.

Extending the recognized catalog with the closure's after-dispatch `detector_timeline` (so a late-pulled citation keeps its kind) was considered and declined: the timeline does not exist at the responder's emit seam (`execute_incident` sees only the dispatch-time `IncidentRequest`), so it would require relocating normalization into the outcome/verification pipeline for a display-only, verdict-identical gain. Instead `.agents/skills/analyze-experiment/references/trajectory-schema.md` notes that a `live-observation` item in a composite trajectory may be a downgraded late-pulled finding.

## What a rerun should show (confirming check — needs an image rebuild; do not run here)

Rebuild the responder image (prompt + normalization are in the image) and rerun the mixed pilot. The four single-fault incidents (i02/i03/i04/i09 types) should show **0 contradicted** causes: `synthetic-reservation` / `sdo-incident-status` / `change-diff` appear as `live-observation` (`verified=null`), the real scenarios stay as verified synthetic-traffic, each cause confirms, and reflection learns a playbook/detector from it. The two composites stay 3/3 confirmed. A cause that cites a real detector that never fired still shows `contradicted`.
