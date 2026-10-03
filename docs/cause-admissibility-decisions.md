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

`SDO_CAUSE_ADMISSIBILITY` (`on` | `off`, default `on`) on the responder Job
selects the gate. `off` restores byte-identical pre-gate behavior — no prompt
paragraph and no filtering — for A/B comparison against a run without the gate.

Default **on** is a deliberate decision: the gate is the intended production
improvement for this thread. The stored n=3 baseline was produced without it;
to reproduce that baseline exactly, set `SDO_CAUSE_ADMISSIBILITY=off`.

## Takeaways

- **Meaning**: the responder no longer asserts a confirmed root cause it can
  only narrate; weakly-evidenced and benign-drift causes are withheld before the
  verifier sees them.
- **Confidence**: high that it cannot drop genuine causes (unit tests cover the
  genuine-composite and late-component cases); the production effect on the D2
  characterization dock is a hypothesis to confirm with a repeat variance run.
- **Implication**: fenced verifier semantics are unchanged; the improvement is
  purely upstream evidence hygiene.
- **Next step**: rerun the composite characterization variance arm with the gate
  on vs. `SDO_CAUSE_ADMISSIBILITY=off` and compare characterization consistency;
  keep the memoryless Codex baseline reused.
