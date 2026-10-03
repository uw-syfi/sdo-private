# Cause-admissibility gate (robustness thread #5)

## Motivation

The responder proposes confirmed root causes; the controller's deterministic
`verify_diagnosis` (`sdo/operational_memory/diagnosis.py`) then checks each one
with a fenced contradiction / red-herring / repair-attribution pipeline. Two
failure modes motivated this work:

1. a responder once over-cited a benign "image drift" as a root cause for i10; and
2. the n=3 variance run showed composite **diagnosis characterization** as the
   floating miss (a composite stage docked to ~66.67%, the "D2 dock"), while
   composition **mitigation** stayed a clean 4/4.

Benign decoys (for example a geo `LOG_LEVEL` env-drift decoy) already correctly
are not cited as a cause, and that must stay true.

## Decision: improve evidence quality upstream, never weaken the verifier

`verify_diagnosis` is fenced and may not be relaxed without explicit user
sign-off. The sanctioned approach is to fix evidence quality on the responder
side. So this thread adds a **responder-side cause-admissibility gate**
(`sdo/agent_runtime/responder/cause_admissibility.py`) that runs strictly
*upstream* of the verifier and can only *remove* causes the responder proposed.
It never relaxes, duplicates, or substitutes for the verifier, and it can never
cause a confirmation the verifier would not otherwise make. The verifier path
is untouched.

Placement: the gate is applied at the single responder choke point,
`execute_incident` in `sdo/agent_runtime/responder/codex.py`, after the agent's
structured `IncidentResult` is validated and before it is returned to the job /
broker. Every caller therefore sees the gated result; nothing downstream
changes.

## The rule (fault-agnostic, regression-safe)

Admissibility is decided only from the production contracts — the responder's
own `IncidentResult` and the controller's `IncidentRequest`. It inspects
evidence *kinds* and whether an explained detector actually fired for this
incident. It reads no benchmark verdict and encodes no fault-specific knowledge
(fairness).

A confirmed root cause is **admissible** when either:

- it cites at least one piece of evidence of a *corroborating* kind
  (`detector-finding`, `synthetic-traffic`, or `state-change`) — evidence an
  independent check can anchor to an incident signal; or
- it names at least one `explained_detector` that actually fired for this
  incident (present in the request's findings or detector history).

A cause is **withheld** only when its entire evidentiary basis is unverifiable
narrative: it cites only `live-observation` items (which the contract documents
as "accepted as live but unverified") *and* explains no detector that fired.

The rule is deliberately conservative, so it cannot drop a genuine cause,
including every component of a real composite (each real component carries a
detector-finding / state-change or explains a firing detector). It targets only
the weakly-evidenced / benign-drift class, which improves diagnosis precision
and makes the set of asserted causes more consistent across runs (addressing the
composite characterization dock). It never adds a cause, so "decoys are not
cited" can only stay as true as before.

Note on the late-landing composite component (N11): a component whose detector
fires just after dispatch can be narrated by the responder. The second
admissibility branch (explains a detector that fired) and the acceptance of a
`state-change` anchor keep such a genuine component admissible, so the gate does
not regress the composite case it is meant to help.

## Prompt guidance

With the gate on, the responder prompt gains a problem-agnostic admissibility
bar: assert a cause only when anchored to a checkable incident signal, route a
benign configuration drift to `static_context` rather than
`confirmed_root_causes`, and do not assert a purely narrative cause. Reducing
how often the deterministic gate has to fire further improves consistency. A
unit test scans the bar for problem-specific vocabulary.

## Configuration and default

`SDO_CAUSE_ADMISSIBILITY` (`on` | `off`, default `off`) on the responder Job
selects the gate. Both modes are fully supported. Default **off** restores
byte-identical pre-gate behavior — no prompt paragraph and no filtering, the
same bytes the A/B/C runs produced — so enabling the gate is opt-in and a run
without it stays reproducible.

Default **off** is a deliberate decision. Default-on would change the SDO
agent's diagnosis behavior in every future experiment run, and the
characterization benefit below is still a hypothesis. We will not land a
paper-affecting behavioral default on an unvalidated hypothesis. Landing the
gate default-off makes this a pure, byte-identical-behavior capability
addition that is safe to merge now; the default-on flip is earned later with
data.

The flip is gated on a **validation arm**: run the composite diagnosis
characterization scenario with `SDO_CAUSE_ADMISSIBILITY=on` versus `off`,
reusing the stored memoryless Codex baseline (do not rerun it while
unchanged). Only a measured, consistent characterization improvement with no
regression (benign decoys still not cited; every genuine composite component
still admitted) earns changing the default to `on`.

## Takeaways

- **Meaning**: when enabled, the responder no longer asserts a confirmed root
  cause it can only narrate; weakly-evidenced and benign-drift causes are
  withheld before the verifier sees them. Shipped default-off, so the capability
  lands without changing any run's behavior.
- **Confidence**: high that it cannot drop genuine causes (unit tests cover the
  genuine-composite and late-component cases); the production effect on the D2
  characterization dock is a hypothesis, which is exactly why the default stays
  off until the validation arm confirms it.
- **Implication**: fenced verifier semantics are unchanged; the improvement is
  purely upstream evidence hygiene, and default-off keeps the merge
  byte-identical to prior behavior.
- **Next step**: run the composite characterization validation arm with the gate
  on vs. `SDO_CAUSE_ADMISSIBILITY=off`, reusing the memoryless Codex baseline; a
  consistent improvement with no regression earns flipping the default to `on`.
