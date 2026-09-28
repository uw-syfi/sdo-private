"""Deterministic verification of a responder's diagnosis.

A confirmed root cause cites live evidence and names the detectors it
explains. After the controller's independent verification, SDO checks both
claims against controller-owned facts:

- every cited detector finding, synthetic scenario, or state change must
  exist in the incident request or in the controller's closing-time diff
  (a live observation cannot be checked and is accepted as live but
  unverified);
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


class DetectorFlip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_id: str
    fired_at_dispatch: bool
    #: None when no post-response evaluation of the detector exists.
    cleared_after_fix: bool | None = None
    flipped: bool


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
    changed = _changed_object_keys(request.state_changes, final_state_changes)
    latest: dict[str, DetectorEvaluation] = {}
    for evaluation in sorted([*final_detector_states, *incident_detector_states], key=lambda item: item.evaluated_at):
        latest[evaluation.detector_id] = evaluation
    repairs = _Repairs.of(result.repair_actions, request.state_changes, final_state_changes, health_cleared_at)
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


def _changed_object_keys(
    dispatch_state_changes: StateChanges | None,
    final_state_changes: StateChanges | None,
) -> set[str] | None:
    """The ``kind/name`` of every object the controller ever saw change.

    ``None`` only when neither diff is available, meaning state-change
    evidence cannot be checked at all (no baseline was ever configured).
    """

    if dispatch_state_changes is None and final_state_changes is None:
        return None
    keys: set[str] = set()
    for state_changes in (dispatch_state_changes, final_state_changes):
        if state_changes is not None:
            keys.update(f"{change.kind}/{change.name}" for change in state_changes.changes)
    return keys


def _verify(
    cause: ConfirmedRootCause,
    fired: set[str],
    scenarios: set[str],
    changed: set[str] | None,
    latest: dict[str, DetectorEvaluation],
    repairs: _Repairs,
) -> RootCauseVerification:
    checks = []
    for item in cause.evidence:
        verified: bool | None
        if item.kind == "detector-finding":
            verified = item.source in fired
        elif item.kind == "synthetic-traffic":
            verified = item.source.removeprefix(SCENARIO_RULE_PREFIX) in scenarios
        elif item.kind == "state-change":
            verified = None if changed is None else item.source in changed
        else:
            verified = None
        checks.append(EvidenceCheck(kind=item.kind, source=item.source, verified=verified))
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
    keys = [_ObjectKey.parse(token) for token in action.target.split() if "/" in token]
    return [key for key in keys if key is not None]


def _diff_keys(state_changes: StateChanges | None) -> list[_ObjectKey] | None:
    if state_changes is None:
        return None
    return [
        _ObjectKey(_kind(change.kind), change.name, "", f"{change.kind}/{change.name}")
        for change in state_changes.changes
    ]


@dataclass(frozen=True)
class _Repairs:
    """The responder's successful, timely repair actions and the baseline changes they left alone."""

    actions: tuple[tuple[str, tuple[_ObjectKey, ...]], ...]
    dispatch_changes: tuple[_ObjectKey, ...]
    externally_reverted: tuple[str, ...]
    health_cleared_at: datetime | None

    @classmethod
    def of(
        cls,
        receipts: Iterable[RepairActionReceipt],
        dispatch_state_changes: StateChanges | None,
        final_state_changes: StateChanges | None,
        health_cleared_at: datetime | None,
    ) -> _Repairs:
        actions = tuple(
            (receipt.action_id, tuple(_action_keys(receipt)))
            for receipt in receipts
            if receipt.success and (health_cleared_at is None or receipt.started_at <= health_cleared_at)
        )
        dispatch = _diff_keys(dispatch_state_changes) or []
        final = _diff_keys(final_state_changes)
        reverted: list[str] = []
        if dispatch_state_changes is not None and final is not None:
            remaining = {(key.kind, key.name) for key in final}
            for key in dispatch:
                if (key.kind, key.name) in remaining:
                    continue
                if not any(touched.matches(key) for _, keys in actions for touched in keys):
                    reverted.append(key.label)
        return cls(actions, tuple(dispatch), tuple(sorted(set(reverted))), health_cleared_at)

    def attribute(self, cause: ConfirmedRootCause) -> RepairAttribution:
        blamed = [_ObjectKey.of(ref) for ref in cause.resources]
        for item in cause.evidence:
            if item.kind == "state-change" and (parsed := _ObjectKey.parse(item.source)) is not None:
                blamed.append(parsed)
        action_ids: list[str] = []
        repaired: list[_ObjectKey] = []
        for action_id, keys in self.actions:
            hits = [key for key in blamed if any(key.matches(touched) for touched in keys)]
            if hits:
                action_ids.append(action_id)
                repaired.extend(key for key in hits if key not in repaired)
        labels = sorted({key.label for key in repaired})
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
                    f"the responder's successful repair actions{timing} touched none of the cause's resources "
                    f"({names})"
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
                reason=f"the responder repaired {', '.join(sorted(set(changed)))}, changed since the healthy baseline",
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
            reason=f"the responder repaired {', '.join(labels)} before health cleared",
        )
