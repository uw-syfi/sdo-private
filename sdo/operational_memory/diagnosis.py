"""Deterministic verification of a responder's diagnosis.

A confirmed root cause cites live evidence and names the detectors it
explains. After the controller's independent verification, SDO checks both
claims against controller-owned facts:

- every cited detector finding, synthetic scenario, or state change must
  exist in the incident request or in the controller's closing-time diff
  (a live observation cannot be checked and is accepted as live but
  unverified). A state change outside the dispatch-time diff must also
  predate the responder's own repair of that object: the closing view holds
  the responder's own edits (a restart's ``restartedAt``), and a change the
  controller first saw after the repair started is that repair, not the
  cause;
- every explained detector must have fired at dispatch and be clear after
  the fix; and
- the responder's own recorded repair must back the cause (F8): a successful
  repair action that started before health cleared must have touched one of
  the cause's resources. When the cause blames an object that changed since
  the healthy baseline, touching that object is enough. Otherwise the cause
  is also refused when the recovery coincides with a baseline change that
  something other than the responder's repair reverted.

A composite fault's later component can land a few seconds after dispatch
(N11), while the incident is still open, so the dispatch-time request diff
alone can miss it even though the responder correctly cited it. Checking
against the union of the request's diff and the controller's diff as of
verification time closes that gap without weakening the check: a change that
was never observed, by dispatch or by verification, is still contradicted.

Without repair attribution, a wrong cause was ``confirmed`` whenever someone
else fixed the real fault inside the verification window, and reflection
learned it. Such a cause is now ``unattributed``. Each cause of a composite
is attributed to its own repair, so a composite with one repaired component
confirms only that component.

The verdict is recorded with the outcome. It does not gate closure, which
the health detectors alone decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from sdo.contracts import DetectorEvaluationStatus, FindingStatus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from sdo.contracts import (
        ConfirmedRootCause,
        DetectorEvaluation,
        IncidentRequest,
        IncidentResult,
        ObjectRef,
        ObservedStateChange,
        RepairActionReceipt,
        StateChanges,
    )

#: Rule-ID prefix of the synthetic-traffic detector's per-scenario findings.
SCENARIO_RULE_PREFIX = "scenario-slo."


class DiagnosisVerdict(str, Enum):
    #: Every explained detector flipped, no cited evidence is contradicted, and
    #: the responder's own repair backs the cause.
    CONFIRMED = "confirmed"
    #: Some cited evidence names something the controller never observed.
    CONTRADICTED = "contradicted"
    #: No contradiction, but the explained detectors did not all flip.
    UNVERIFIED = "unverified"
    #: Health cleared, but the responder's recorded repair does not back this
    #: cause: it touched none of the cause's resources before health cleared,
    #: or something else reverted the configuration change that recovered.
    UNATTRIBUTED = "unattributed"
    #: A root cause recorded before evidence was required.
    NO_EVIDENCE = "no-evidence"


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    source: str
    #: True when the reference exists, False when it does not, None when it cannot be checked.
    verified: bool | None = None
    #: Why a check failed, when the reference existed but is not evidence (for
    #: example, a state change first observed after the responder's own repair).
    reason: str | None = None


class DetectorFlip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_id: str
    fired_at_dispatch: bool
    #: None when no post-response evaluation of the detector exists.
    cleared_after_fix: bool | None = None
    flipped: bool


#: How far past ``health_cleared_at`` a responder-reported repair start may be
#: and still be credited, when the controller saw the repaired object revert
#: during the responder's session. Models read the clock after the fact, so a
#: real repair's reported start can trail the controller's record by seconds;
#: beyond this the report is not trusted (F8 stays strict).
REPAIR_CLOCK_SKEW = timedelta(seconds=30)


class RepairAttribution(BaseModel):
    """Whether the responder's own recorded repair backs one root cause (F8)."""

    model_config = ConfigDict(extra="forbid")

    attributed: bool
    #: IDs of the successful, timely repair actions that touched the cause's resources.
    actions: list[str] = Field(default_factory=list)
    #: The cause's resources those actions touched, as ``Kind/name``.
    repaired_resources: list[str] = Field(default_factory=list)
    #: Baseline changes present at dispatch, gone at verification, that no
    #: responder repair touched, as ``Kind/name``.
    externally_reverted: list[str] = Field(default_factory=list)
    #: IDs, among ``actions``, whose reported ``started_at`` fell after
    #: ``health_cleared_at`` but within :data:`REPAIR_CLOCK_SKEW`, credited
    #: because the controller saw the object revert during the responder's
    #: session. Empty when every credited action started on time.
    clock_skew_corrected: list[str] = Field(default_factory=list)
    reason: str


class RootCauseVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    verdict: DiagnosisVerdict
    evidence: list[EvidenceCheck] = Field(default_factory=list)
    detectors: list[DetectorFlip] = Field(default_factory=list)
    #: None only in records written before repair attribution existed.
    repair: RepairAttribution | None = None


def verify_diagnosis(
    request: IncidentRequest,
    result: IncidentResult | None,
    *,
    final_detector_states: Iterable[DetectorEvaluation],
    incident_detector_states: Iterable[DetectorEvaluation] = (),
    final_state_changes: StateChanges | None = None,
    health_cleared_at: datetime | None = None,
    observed_state_changes: Iterable[ObservedStateChange] | None = None,
    dispatched_at: datetime | None = None,
    responder_completed_at: datetime | None = None,
) -> list[RootCauseVerification]:
    """Verify each confirmed root cause of ``result`` against controller facts.

    ``final_state_changes`` is the controller's diff against the healthy
    baseline as of verification time (its closing view, N11), which can name
    a composite's later fault that landed after the request's dispatch-time
    snapshot was taken. State-change evidence is checked against the union of
    the request's diff and this one; a change absent from both is still
    contradicted.

    ``health_cleared_at`` is the controller's record of when the health
    detectors began their final clear streak. Only a repair action that
    started by then can back a cause. ``None`` (a controller that predates
    the fact) skips the timing check.

    ``observed_state_changes`` is every object the controller saw differ from
    the baseline while the incident was open, with when it first saw it. With
    it, a state-change citation outside the dispatch-time diff counts only
    if the controller observed it before the responder's first repair action
    on that object started, so a wrong fix cannot cite its own edit. It is
    also how a late fault the responder reverted exactly, gone from both
    diffs, still verifies. ``None`` (a controller that predates it) keeps the
    union of the two diffs.

    ``dispatched_at`` and ``responder_completed_at`` are the controller's
    record of the responder's session. With them, a successful repair whose
    self-reported ``started_at`` trails ``health_cleared_at`` by at most
    :data:`REPAIR_CLOCK_SKEW` still backs the dispatch-diff objects it lists
    that the controller saw reverted by closure (a model reads its clock after
    the mutation, so the report can lag the controller's record). It never
    backs a fault still present, an object outside the dispatch diff, or an
    action reported outside the session. Without them the strict rule holds.
    """

    if result is None:
        return []
    fired = {finding.detector_id for finding in request.findings if finding.status == FindingStatus.ACTIVE} | {
        evaluation.detector_id
        for evaluation in request.detector_history
        if evaluation.status == DetectorEvaluationStatus.FIRING
    }
    scenarios = {
        finding.rule_id.removeprefix(SCENARIO_RULE_PREFIX)
        for finding in request.findings
        if finding.rule_id.startswith(SCENARIO_RULE_PREFIX)
    }
    changed = _StateChangeEvidence.of(
        request.state_changes, final_state_changes, observed_state_changes, result.repair_actions
    )
    latest: dict[str, DetectorEvaluation] = {}
    for evaluation in sorted([*final_detector_states, *incident_detector_states], key=lambda item: item.evaluated_at):
        latest[evaluation.detector_id] = evaluation
    session = (
        None if dispatched_at is None or responder_completed_at is None else (dispatched_at, responder_completed_at)
    )
    repairs = _Repairs.of(result.repair_actions, request.state_changes, final_state_changes, health_cleared_at, session)
    return [_verify(cause, fired, scenarios, changed, latest, repairs) for cause in result.confirmed_root_causes]


def recovered_by_responder(verifications: Iterable[RootCauseVerification]) -> bool | None:
    """Whether the responder's own repair backs at least one verified cause.

    ``None`` when no cause carries an attribution (no causes, or records that
    predate it).
    """

    attributions = [verification.repair for verification in verifications if verification.repair is not None]
    if not attributions:
        return None
    return any(attribution.attributed for attribution in attributions)


@dataclass(frozen=True)
class _StateChangeEvidence:
    """What the controller saw change, and when, for checking ``state-change`` citations."""

    #: ``Kind/name`` in the dispatch-time diff: observed before any repair.
    dispatch: frozenset[str]
    #: ``Kind/name`` in either diff (N11's union), for records without observation times.
    changed: frozenset[str]
    #: ``Kind/name`` to first observation while the incident was open; None in older records.
    observed: dict[str, datetime] | None
    #: Every recorded repair action with the objects it names, successful or not.
    actions: tuple[tuple[RepairActionReceipt, tuple[_ObjectKey, ...]], ...]
    #: False when no diff and no observation exist: no baseline was ever configured.
    checkable: bool

    @classmethod
    def of(
        cls,
        dispatch_state_changes: StateChanges | None,
        final_state_changes: StateChanges | None,
        observed_state_changes: Iterable[ObservedStateChange] | None,
        receipts: Iterable[RepairActionReceipt],
    ) -> _StateChangeEvidence:
        dispatch = frozenset(_labels(dispatch_state_changes))
        changed = dispatch | frozenset(_labels(final_state_changes))
        observed = None
        if observed_state_changes is not None:
            observed = {}
            for item in observed_state_changes:
                label = f"{item.kind}/{item.name}"
                if label not in observed or item.first_observed_at < observed[label]:
                    observed[label] = item.first_observed_at
        actions = tuple((receipt, tuple(_action_keys(receipt))) for receipt in receipts)
        checkable = dispatch_state_changes is not None or final_state_changes is not None or observed is not None
        return cls(dispatch, changed, observed, actions, checkable)

    def check(self, source: str) -> tuple[bool | None, str | None]:
        if not self.checkable:
            return None, None
        if source in self.dispatch:
            return True, None
        if self.observed is None:
            return source in self.changed, None
        first_observed = self.observed.get(source)
        if first_observed is None:
            return False, "the controller never observed this object change while the incident was open"
        cited = _ObjectKey.parse(source)
        own = sorted(
            (
                (receipt.started_at, receipt.action_id)
                for receipt, keys in self.actions
                if cited is not None and any(cited.matches(key) for key in keys)
            ),
        )
        if own and first_observed >= own[0][0]:
            started_at, action_id = own[0]
            return False, (
                f"first observed at {first_observed.isoformat()}, after the responder's own repair "
                f"{action_id} of it started at {started_at.isoformat()}: the change is that repair, not the cause"
            )
        return True, None


def _labels(state_changes: StateChanges | None) -> list[str]:
    return [] if state_changes is None else [f"{change.kind}/{change.name}" for change in state_changes.changes]


def _verify(
    cause: ConfirmedRootCause,
    fired: set[str],
    scenarios: set[str],
    changed: _StateChangeEvidence,
    latest: dict[str, DetectorEvaluation],
    repairs: _Repairs,
) -> RootCauseVerification:
    checks = []
    for item in cause.evidence:
        verified: bool | None
        reason: str | None = None
        if item.kind == "detector-finding":
            verified = item.source in fired
        elif item.kind == "synthetic-traffic":
            verified = item.source.removeprefix(SCENARIO_RULE_PREFIX) in scenarios
        elif item.kind == "state-change":
            verified, reason = changed.check(item.source)
        else:
            verified = None
        checks.append(EvidenceCheck(kind=item.kind, source=item.source, verified=verified, reason=reason))
    flips = []
    for detector_id in cause.explained_detectors:
        evaluation = latest.get(detector_id)
        cleared = None if evaluation is None else evaluation.status == DetectorEvaluationStatus.CLEAR
        was_firing = detector_id in fired
        flips.append(
            DetectorFlip(
                detector_id=detector_id,
                fired_at_dispatch=was_firing,
                cleared_after_fix=cleared,
                flipped=was_firing and cleared is True,
            )
        )
    repair = repairs.attribute(cause)
    if not cause.evidence:
        verdict = DiagnosisVerdict.NO_EVIDENCE
    elif any(check.verified is False for check in checks):
        verdict = DiagnosisVerdict.CONTRADICTED
    elif flips and all(flip.flipped for flip in flips):
        verdict = DiagnosisVerdict.CONFIRMED if repair.attributed else DiagnosisVerdict.UNATTRIBUTED
    else:
        verdict = DiagnosisVerdict.UNVERIFIED
    return RootCauseVerification(
        summary=cause.summary, verdict=verdict, evidence=checks, detectors=flips, repair=repair
    )


# Repair attribution (F8).

#: kubectl short names and plurals, mapped to the lower-case singular kind.
_KIND_ALIASES = {
    "cm": "configmap",
    "configmaps": "configmap",
    "cronjobs": "cronjob",
    "cj": "cronjob",
    "daemonsets": "daemonset",
    "deploy": "deployment",
    "deployments": "deployment",
    "ds": "daemonset",
    "hpa": "horizontalpodautoscaler",
    "horizontalpodautoscalers": "horizontalpodautoscaler",
    "ing": "ingress",
    "ingresses": "ingress",
    "jobs": "job",
    "netpol": "networkpolicy",
    "networkpolicies": "networkpolicy",
    "persistentvolumeclaims": "persistentvolumeclaim",
    "po": "pod",
    "pods": "pod",
    "pvc": "persistentvolumeclaim",
    "replicasets": "replicaset",
    "rolebindings": "rolebinding",
    "roles": "role",
    "rs": "replicaset",
    "sa": "serviceaccount",
    "secrets": "secret",
    "serviceaccounts": "serviceaccount",
    "services": "service",
    "statefulsets": "statefulset",
    "sts": "statefulset",
    "svc": "service",
}
_KNOWN_KINDS = frozenset(_KIND_ALIASES.values()) | {"service", "secret", "role", "rolebinding"}
#: Pod-owning kinds: a Pod named ``<owner>-...`` belongs to its owner for attribution.
_POD_OWNERS = frozenset({"deployment", "statefulset", "daemonset", "replicaset", "job"})


def _kind(raw: str) -> str:
    kind = raw.strip().lower().split(".", 1)[0]
    return _KIND_ALIASES.get(kind, kind)


@dataclass(frozen=True)
class _ObjectKey:
    kind: str
    name: str
    namespace: str = ""
    #: The ``Kind/name`` spelling recorded in the verification.
    label: str = ""

    @classmethod
    def of(cls, ref: ObjectRef) -> _ObjectKey:
        return cls(_kind(ref.kind), ref.name, ref.namespace, f"{ref.kind}/{ref.name}")

    @classmethod
    def parse(cls, text: str) -> _ObjectKey | None:
        """``Kind/name``, ``namespace/Kind/name`` or ``Kind/namespace/name``; ``None`` otherwise."""

        parts = [part for part in text.strip().split("/") if part]
        if len(parts) == 2:
            return cls(_kind(parts[0]), parts[1], "", f"{parts[0]}/{parts[1]}")
        if len(parts) == 3:
            if _kind(parts[0]) in _KNOWN_KINDS:
                return cls(_kind(parts[0]), parts[2], parts[1], f"{parts[0]}/{parts[2]}")
            if _kind(parts[1]) in _KNOWN_KINDS:
                return cls(_kind(parts[1]), parts[2], parts[0], f"{parts[1]}/{parts[2]}")
        return None

    def matches(self, other: _ObjectKey) -> bool:
        if self.namespace and other.namespace and self.namespace != other.namespace:
            return False
        if self.kind == other.kind:
            return self.name == other.name
        return _owns(self, other) or _owns(other, self)


def _owns(owner: _ObjectKey, owned: _ObjectKey) -> bool:
    if owner.kind not in _POD_OWNERS or owned.kind not in {"pod", "replicaset"}:
        return False
    return owned.name.startswith(owner.name + "-")


def _action_keys(action: RepairActionReceipt) -> list[_ObjectKey]:
    if action.resources:
        return [_ObjectKey.of(ref) for ref in action.resources]
    tokens = action.target.replace(",", " ").split()
    keys = [_ObjectKey.parse(token) for token in tokens if "/" in token]
    return [key for key in keys if key is not None]


def _diff_keys(state_changes: StateChanges | None) -> list[_ObjectKey] | None:
    if state_changes is None:
        return None
    return [
        _ObjectKey(_kind(change.kind), change.name, "", f"{change.kind}/{change.name}")
        for change in state_changes.changes
    ]


def _skew_creditable(
    receipt: RepairActionReceipt,
    keys: tuple[_ObjectKey, ...],
    dispatch: list[_ObjectKey],
    remaining: set[tuple[str, str]] | None,
    health_cleared_at: datetime,
    session: tuple[datetime, datetime] | None,
) -> tuple[_ObjectKey, ...]:
    """The objects a late-reported repair may still be credited with, or ``()``.

    Only inside the bounded skew of ``health_cleared_at`` and the responder's
    session, and only for objects the controller saw change at dispatch and
    gone by closure: its own observations, not the model's clock, show the
    revert happened while the responder was working.
    """

    if session is None or remaining is None:
        return ()
    dispatched_at, responder_completed_at = session
    if receipt.started_at > health_cleared_at + REPAIR_CLOCK_SKEW:
        return ()
    if not dispatched_at - REPAIR_CLOCK_SKEW <= receipt.started_at <= responder_completed_at + REPAIR_CLOCK_SKEW:
        return ()
    return tuple(
        key for key in keys if (key.kind, key.name) not in remaining and any(key.matches(change) for change in dispatch)
    )


@dataclass(frozen=True)
class _Repairs:
    """The responder's successful, timely repair actions and the baseline changes they left alone."""

    actions: tuple[tuple[str, tuple[_ObjectKey, ...]], ...]
    dispatch_changes: tuple[_ObjectKey, ...]
    externally_reverted: tuple[str, ...]
    health_cleared_at: datetime | None
    #: Action IDs credited only through the bounded clock-skew rule.
    skew_corrected: frozenset[str] = frozenset()

    @classmethod
    def of(
        cls,
        receipts: Iterable[RepairActionReceipt],
        dispatch_state_changes: StateChanges | None,
        final_state_changes: StateChanges | None,
        health_cleared_at: datetime | None,
        session: tuple[datetime, datetime] | None = None,
    ) -> _Repairs:
        dispatch = _diff_keys(dispatch_state_changes) or []
        final = _diff_keys(final_state_changes)
        remaining = None if final is None else {(key.kind, key.name) for key in final}
        actions: list[tuple[str, tuple[_ObjectKey, ...]]] = []
        skew_corrected: set[str] = set()
        for receipt in receipts:
            if not receipt.success:
                continue
            keys = tuple(_action_keys(receipt))
            if health_cleared_at is None or receipt.started_at <= health_cleared_at:
                actions.append((receipt.action_id, keys))
                continue
            reverted = _skew_creditable(receipt, keys, dispatch, remaining, health_cleared_at, session)
            if reverted:
                actions.append((receipt.action_id, reverted))
                skew_corrected.add(receipt.action_id)
        reverted_labels: list[str] = []
        if dispatch_state_changes is not None and remaining is not None:
            for key in dispatch:
                if (key.kind, key.name) in remaining:
                    continue
                if not any(touched.matches(key) for _, keys in actions for touched in keys):
                    reverted_labels.append(key.label)
        return cls(
            tuple(actions),
            tuple(dispatch),
            tuple(sorted(set(reverted_labels))),
            health_cleared_at,
            frozenset(skew_corrected),
        )

    def attribute(self, cause: ConfirmedRootCause) -> RepairAttribution:
        cited = [_ObjectKey.parse(item.source) for item in cause.evidence if item.kind == "state-change"]
        blamed = [_ObjectKey.of(ref) for ref in cause.resources] + [key for key in cited if key is not None]
        action_ids: list[str] = []
        repaired: list[_ObjectKey] = []
        for action_id, keys in self.actions:
            hits = [key for key in blamed if any(key.matches(touched) for touched in keys)]
            if hits:
                action_ids.append(action_id)
                repaired.extend(key for key in hits if key not in repaired)
        labels = sorted({key.label for key in repaired})
        skewed = [action_id for action_id in action_ids if action_id in self.skew_corrected]
        skew_note = (
            ""
            if not skewed
            else (
                f" (the start reported by {', '.join(skewed)} trails health_cleared_at by under "
                f"{int(REPAIR_CLOCK_SKEW.total_seconds())} s; credited because the controller saw the object "
                "revert during the responder's session)"
            )
        )
        if not action_ids:
            names = ", ".join(sorted({key.label for key in blamed})) or "none recorded"
            timing = (
                ""
                if self.health_cleared_at is None
                else f" that started before health cleared at {self.health_cleared_at.isoformat()}"
            )
            return RepairAttribution(
                attributed=False,
                externally_reverted=list(self.externally_reverted),
                reason=(
                    f"the responder's successful repair actions{timing} touched none of the cause's resources ({names})"
                ),
            )
        changed = [
            key.label
            for key in repaired
            if any(key.kind == change.kind and key.name == change.name for change in self.dispatch_changes)
        ]
        if changed:
            return RepairAttribution(
                attributed=True,
                actions=action_ids,
                repaired_resources=labels,
                externally_reverted=list(self.externally_reverted),
                clock_skew_corrected=skewed,
                reason=(
                    f"the responder repaired {', '.join(sorted(set(changed)))}, changed since the healthy baseline"
                    f"{skew_note}"
                ),
            )
        if self.externally_reverted:
            return RepairAttribution(
                attributed=False,
                actions=action_ids,
                repaired_resources=labels,
                externally_reverted=list(self.externally_reverted),
                reason=(
                    "health recovered while baseline changes no responder repair touched were reverted: "
                    f"{', '.join(self.externally_reverted)}; the repair of {', '.join(labels)} does not explain "
                    "the recovery"
                ),
            )
        return RepairAttribution(
            attributed=True,
            actions=action_ids,
            repaired_resources=labels,
            clock_skew_corrected=skewed,
            reason=f"the responder repaired {', '.join(labels)} before health cleared{skew_note}",
        )
