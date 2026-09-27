from __future__ import annotations

from pathlib import Path

from sdo.contracts import IncidentRequest, PriorOutcomeEvidence
from sdo.contracts.models import Finding, FindingSeverity, FindingStatus, ObjectRef, SurfacedPlaybook
from sdo.operational_memory.models import DiagnosticsManifest
from sdo.operational_memory.warm_path import (
    SOURCE_ACTIVE_FINDING,
    SOURCE_LEARNED_FROM_PRIOR,
    SOURCE_PRIOR_APPLIED,
    warm_playbook_matches,
)

HEALTH_PLAYBOOK = ".sdo/playbooks/health-objective/README.md"
INCIDENT_PLAYBOOK = ".sdo/playbooks/missing-geo-mongo-init-configmap/README.md"
PRIOR_INCIDENT = "inc-prior"


def _fixture_request() -> IncidentRequest:
    root = Path(__file__).resolve().parents[4]
    text = (root / "tests" / "fixtures" / "sdo" / "contracts" / "incident_request.json").read_text(encoding="utf-8")
    return IncidentRequest.model_validate_json(text)


def _detector(
    detector_id: str,
    *,
    detector_class: str,
    playbooks: list[str],
    originating_incident: str | None = None,
) -> dict[str, object]:
    registration: dict[str, object] = {
        "id": detector_id,
        "package": f"./detectors/{'health' if detector_class == 'health' else 'incidents'}/x",
        "class": detector_class,
        "owner": "health_judge" if detector_class == "health" else "responder",
        "watches": [{"apiVersion": "apps/v1", "kind": "Deployment"}],
        "interval": "30s",
        "persistence": {"firing": 1, "clearing": 2},
        "batching": {"severity": "critical", "debounce": "500ms"},
        "possiblePlaybooks": playbooks,
        "originatingCommit": "abc123",
    }
    if originating_incident is not None:
        registration["originatingIncident"] = originating_incident
    return registration


def _manifest(*, incident_playbooks: list[str], originating_incident: str = PRIOR_INCIDENT) -> DiagnosticsManifest:
    return DiagnosticsManifest.model_validate(
        {
            "apiVersion": "sdo.dev/v1alpha1",
            "kind": "DetectorManifest",
            "sdkVersion": "v0.1",
            "detectors": [
                _detector("health-objective", detector_class="health", playbooks=[HEALTH_PLAYBOOK]),
                _detector(
                    "missing-geo-mongo-init-configmap",
                    detector_class="incident",
                    playbooks=incident_playbooks,
                    originating_incident=originating_incident,
                ),
            ],
        }
    )


def _health_finding() -> Finding:
    return Finding(
        detector_id="health-objective",
        rule_id="required-configmap-missing",
        status=FindingStatus.ACTIVE,
        severity=FindingSeverity.CRITICAL,
        summary="required ConfigMap missing",
        evidence="mongodb-geo mounts absent mongo-geo-script",
        primary_resource=ObjectRef(api_version="v1", kind="ConfigMap", namespace="hr", name="mongo-geo-script"),
        playbooks=[HEALTH_PLAYBOOK],
        fingerprint="health-objective/required-configmap-missing/hr/mongo-geo-script",
    )


def _request(
    *,
    match_reason: str = "exact-fingerprint",
    prior_applied: list[str] | None = None,
    incident_finding: bool = False,
) -> IncidentRequest:
    """A v2-shaped request: only the health detector fired; the incident detector has not."""

    findings = [_health_finding()]
    if incident_finding:
        findings.append(
            _health_finding().model_copy(
                update={
                    "detector_id": "missing-geo-mongo-init-configmap",
                    "rule_id": "missing-geo-mongo-init-configmap",
                    "playbooks": [INCIDENT_PLAYBOOK],
                    "fingerprint": "missing-geo-mongo-init-configmap/hr/mongodb-geo",
                }
            )
        )
    prior = PriorOutcomeEvidence(
        incident_id=PRIOR_INCIDENT,
        match_reason=match_reason,  # type: ignore[arg-type]
        applied_playbooks=[HEALTH_PLAYBOOK] if prior_applied is None else prior_applied,
        source_commit="1111111111111111111111111111111111111111",
        exact_source_match=False,
    )
    return _fixture_request().model_copy(
        update={
            "findings": findings,
            "surfaced_playbooks": [SurfacedPlaybook(path=HEALTH_PLAYBOOK)],
            "relevant_outcomes": [prior],
        }
    )


def test_exact_prior_with_registered_but_not_fired_incident_detector_is_warm() -> None:
    matches = warm_playbook_matches(_request(), _manifest(incident_playbooks=[INCIDENT_PLAYBOOK]))

    assert [match.path for match in matches] == [INCIDENT_PLAYBOOK]
    match = matches[0]
    assert match.detector.id == "missing-geo-mongo-init-configmap"
    assert match.detector_fired is False
    assert match.sources == (SOURCE_LEARNED_FROM_PRIOR,)
    assert match.prior_incidents == (PRIOR_INCIDENT,)


def test_prior_applied_incident_playbook_is_warm_without_originating_link() -> None:
    matches = warm_playbook_matches(
        _request(prior_applied=[INCIDENT_PLAYBOOK]),
        _manifest(incident_playbooks=[INCIDENT_PLAYBOOK], originating_incident="some-other-incident"),
    )

    assert [(match.path, match.sources) for match in matches] == [(INCIDENT_PLAYBOOK, (SOURCE_PRIOR_APPLIED,))]


def test_fired_incident_detector_reports_its_findings() -> None:
    matches = warm_playbook_matches(_request(incident_finding=True), _manifest(incident_playbooks=[INCIDENT_PLAYBOOK]))

    assert len(matches) == 1
    assert matches[0].detector_fired is True
    assert SOURCE_ACTIVE_FINDING in matches[0].sources


def test_non_exact_prior_is_cold() -> None:
    request = _request(match_reason="detector-rule-resource-kind", prior_applied=[INCIDENT_PLAYBOOK])

    assert warm_playbook_matches(request, _manifest(incident_playbooks=[INCIDENT_PLAYBOOK])) == []


def test_playbook_not_registered_to_an_incident_detector_is_cold() -> None:
    request = _request(prior_applied=[INCIDENT_PLAYBOOK])

    assert warm_playbook_matches(request, _manifest(incident_playbooks=[])) == []


def test_unrelated_incident_detector_does_not_make_its_playbook_warm() -> None:
    manifest = _manifest(incident_playbooks=[INCIDENT_PLAYBOOK], originating_incident="some-other-incident")

    assert warm_playbook_matches(_request(), manifest) == []


def test_health_owned_playbook_is_cold_even_when_surfaced_and_applied() -> None:
    request = _request(prior_applied=[HEALTH_PLAYBOOK])
    manifest = _manifest(incident_playbooks=[], originating_incident="some-other-incident")

    assert warm_playbook_matches(request, manifest) == []
