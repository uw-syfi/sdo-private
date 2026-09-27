"""Deterministic recognition of a warm, exact-match incident.

An incident is *warm* when the controller matched a prior verified success by
exact finding fingerprint and that match surfaces a playbook owned by a
registered, responder-owned incident detector (the playbook is listed in the
detector's ``possiblePlaybooks``). Such detectors and playbooks were validated
by the commit broker and learned from a prior independently verified success.

The owning detector need not have fired yet: a learned detector can lag the
health detectors that trigger dispatch. A playbook is surfaced for the
incident when any of these holds:

- an active finding of its owning incident detector lists it;
- the controller surfaced it in the incident request;
- an exact-fingerprint prior outcome applied it; or
- its owning incident detector was learned from an exact-fingerprint prior
  outcome (``originatingIncident``).

The responder uses the rule to skip re-diagnosis, and the broker uses it to
skip redundant reflection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sdo.contracts.models import FindingStatus
from sdo.operational_memory.models import ArtifactOwner

if TYPE_CHECKING:
    from sdo.contracts import Finding, IncidentRequest, PriorOutcomeEvidence
    from sdo.operational_memory.models import DetectorRegistration, DiagnosticsManifest

EXACT_FINGERPRINT_MATCH = "exact-fingerprint"

#: Why a warm playbook is surfaced for the incident.
SOURCE_ACTIVE_FINDING = "active-finding"
SOURCE_SURFACED = "surfaced"
SOURCE_PRIOR_APPLIED = "prior-outcome-applied"
SOURCE_LEARNED_FROM_PRIOR = "detector-learned-from-exact-prior"


@dataclass(frozen=True)
class WarmPlaybookMatch:
    """An incident-detector-owned playbook surfaced by an exact-fingerprint prior success."""

    path: str
    detector: DetectorRegistration
    #: Active findings of the owning detector; empty when it has not fired.
    findings: tuple[Finding, ...]
    sources: tuple[str, ...]
    prior_incidents: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("warm playbook path must not be empty")
        if not self.sources:
            raise ValueError("a warm playbook needs at least one surfacing source")
        if not self.prior_incidents:
            raise ValueError("a warm playbook needs an exact-fingerprint prior incident")

    @property
    def detector_fired(self) -> bool:
        return bool(self.findings)


def exact_prior_outcomes(request: IncidentRequest) -> list[PriorOutcomeEvidence]:
    return [outcome for outcome in request.relevant_outcomes if outcome.match_reason == EXACT_FINGERPRINT_MATCH]


def has_exact_fingerprint_match(request: IncidentRequest) -> bool:
    return bool(exact_prior_outcomes(request))


def warm_playbook_matches(request: IncidentRequest, manifest: DiagnosticsManifest) -> list[WarmPlaybookMatch]:
    """Incident-detector-owned playbooks surfaced for an exact-match incident, sorted by path."""

    exact = exact_prior_outcomes(request)
    if not exact:
        return []
    prior_incidents = tuple(sorted({outcome.incident_id for outcome in exact}))
    prior_applied = {path for outcome in exact for path in outcome.applied_playbooks}
    surfaced = {playbook.path for playbook in request.surfaced_playbooks}
    matches: list[WarmPlaybookMatch] = []
    for detector in manifest.detectors:
        if detector.detector_class != "incident" or detector.owner != ArtifactOwner.RESPONDER:
            continue
        findings = tuple(
            finding
            for finding in request.findings
            if finding.status == FindingStatus.ACTIVE and finding.detector_id == detector.id
        )
        finding_playbooks = {path for finding in findings for path in finding.playbooks}
        learned_from_prior = detector.originating_incident in prior_incidents
        for path in detector.possible_playbooks:
            sources = [
                source
                for source, holds in (
                    (SOURCE_ACTIVE_FINDING, path in finding_playbooks),
                    (SOURCE_SURFACED, path in surfaced),
                    (SOURCE_PRIOR_APPLIED, path in prior_applied),
                    (SOURCE_LEARNED_FROM_PRIOR, learned_from_prior),
                )
                if holds
            ]
            if sources:
                matches.append(
                    WarmPlaybookMatch(
                        path=path,
                        detector=detector,
                        findings=findings,
                        sources=tuple(sources),
                        prior_incidents=prior_incidents,
                    )
                )
    return sorted(matches, key=lambda match: (match.path, match.detector.id))
