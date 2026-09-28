"""Deterministic verification of a responder's diagnosis.

A confirmed root cause cites live evidence and names the detectors it
explains. After the controller's independent verification, SDO checks both
claims against controller-owned facts:

- every cited detector finding, synthetic scenario, or state change must
  exist in the incident request (a live observation cannot be checked and is
  accepted as live but unverified); and
- every explained detector must have fired at dispatch and be clear after
  the fix.

The verdict is recorded with the outcome. It does not gate closure, which
the health detectors alone decide.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from sdo.contracts import DetectorEvaluationStatus, FindingStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sdo.contracts import ConfirmedRootCause, DetectorEvaluation, IncidentRequest, IncidentResult

#: Rule-ID prefix of the synthetic-traffic detector's per-scenario findings.
SCENARIO_RULE_PREFIX = "scenario-slo."


class DiagnosisVerdict(str, Enum):
    #: Every explained detector flipped and no cited evidence is contradicted.
    CONFIRMED = "confirmed"
    #: Some cited evidence names something the controller never observed.
    CONTRADICTED = "contradicted"
    #: No contradiction, but the explained detectors did not all flip.
    UNVERIFIED = "unverified"
    #: A root cause recorded before evidence was required.
    NO_EVIDENCE = "no-evidence"


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    source: str
    #: True when the reference exists, False when it does not, None when it cannot be checked.
    verified: bool | None


class DetectorFlip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_id: str
    fired_at_dispatch: bool
    #: None when no post-response evaluation of the detector exists.
    cleared_after_fix: bool | None
    flipped: bool


class RootCauseVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    verdict: DiagnosisVerdict
    evidence: list[EvidenceCheck] = Field(default_factory=list)
    detectors: list[DetectorFlip] = Field(default_factory=list)


def verify_diagnosis(
    request: IncidentRequest,
    result: IncidentResult | None,
    *,
    final_detector_states: Iterable[DetectorEvaluation],
    incident_detector_states: Iterable[DetectorEvaluation] = (),
) -> list[RootCauseVerification]:
    """Verify each confirmed root cause of ``result`` against controller facts."""

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
    changed = (
        None
        if request.state_changes is None
        else {f"{change.kind}/{change.name}" for change in request.state_changes.changes}
    )
    latest: dict[str, DetectorEvaluation] = {}
    for evaluation in sorted([*final_detector_states, *incident_detector_states], key=lambda item: item.evaluated_at):
        latest[evaluation.detector_id] = evaluation
    return [_verify(cause, fired, scenarios, changed, latest) for cause in result.confirmed_root_causes]


def _verify(
    cause: ConfirmedRootCause,
    fired: set[str],
    scenarios: set[str],
    changed: set[str] | None,
    latest: dict[str, DetectorEvaluation],
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
    if not cause.evidence:
        verdict = DiagnosisVerdict.NO_EVIDENCE
    elif any(check.verified is False for check in checks):
        verdict = DiagnosisVerdict.CONTRADICTED
    elif flips and all(flip.flipped for flip in flips):
        verdict = DiagnosisVerdict.CONFIRMED
    else:
        verdict = DiagnosisVerdict.UNVERIFIED
    return RootCauseVerification(summary=cause.summary, verdict=verdict, evidence=checks, detectors=flips)
