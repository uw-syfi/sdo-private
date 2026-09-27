"""Deterministic recognition of a warm, exact-match incident.

An incident is *warm* when an active finding comes from a responder-owned
incident detector (validated by the commit broker and learned from a prior
independently verified success) and the controller also matched a prior
verified success by exact finding fingerprint. The responder uses this to
skip re-diagnosis, and the broker uses it to skip redundant reflection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sdo.contracts.models import FindingStatus
from sdo.operational_memory.models import ArtifactOwner

if TYPE_CHECKING:
    from sdo.contracts import Finding, IncidentRequest
    from sdo.operational_memory.models import DiagnosticsManifest

EXACT_FINGERPRINT_MATCH = "exact-fingerprint"


def has_exact_fingerprint_match(request: IncidentRequest) -> bool:
    return any(outcome.match_reason == EXACT_FINGERPRINT_MATCH for outcome in request.relevant_outcomes)


def warm_incident_findings(request: IncidentRequest, manifest: DiagnosticsManifest) -> list[Finding]:
    """Active incident-detector findings with a playbook, when a prior success matched exactly."""

    if not has_exact_fingerprint_match(request):
        return []
    incident_detectors = {
        detector.id
        for detector in manifest.detectors
        if detector.detector_class == "incident" and detector.owner == ArtifactOwner.RESPONDER
    }
    return [
        finding
        for finding in request.findings
        if finding.status == FindingStatus.ACTIVE and finding.detector_id in incident_detectors and finding.playbooks
    ]
