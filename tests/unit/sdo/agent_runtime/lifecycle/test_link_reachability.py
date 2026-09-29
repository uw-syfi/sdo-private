"""Link reachability: the judge declares dependency edges; the lifecycle installs a link detector."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from controller.builder.manifest import load_manifest
from sdo.agent_runtime.lifecycle.agents import TRAFFIC_AUTHORING, AuthoredTrafficFile
from sdo.agent_runtime.lifecycle.operational_memory import (
    _traffic_detector_source,
    _validate_health_judge_artifact,
    ensure_operational_memory,
)
from sdo.operational_memory.models import TrafficWorkload
from sdo.operational_memory.repository import MemoryRepository
from tests.unit.sdo.agent_runtime.lifecycle.test_traffic_lifecycle import OBJECTIVE, _files, _judged

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
NEUTRAL_SOURCES = (
    "controller/sdk/traffic/link.go",
    "controller/runtime/prober/link.go",
    "controller/runtime/traffic_observer.go",
)
BENCHMARK_TOKENS = ("networkpolicy", "network policy", "network_policy", "sregym", "deny-all", "calico", "kind cluster")


def _links(*edges: tuple[str, str, int], **extra: object) -> str:
    document: dict[str, object] = {
        "apiVersion": "sdo.dev/v1alpha1",
        "kind": "TrafficWorkload",
        "name": "links",
        "purpose": "link-probe",
        "links": [{"from": source, "to": target, "port": port} for source, target, port in edges],
        **extra,
    }
    return yaml.safe_dump(document)


def _parse(text: str) -> TrafficWorkload:
    return TrafficWorkload.model_validate(yaml.safe_load(text))


def test_link_probe_workload_declares_edges_with_from_and_to() -> None:
    workload = _parse(_links(("frontend", "recommendation", 8085), ("frontend", "user", 8086), failures=4))

    assert workload.purpose == "link-probe"
    assert [(link.source, link.target, link.port) for link in workload.links] == [
        ("frontend", "recommendation", 8085),
        ("frontend", "user", 8086),
    ]
    assert workload.failures == 4
    assert workload.scenarios == []


@pytest.mark.parametrize(
    "text",
    [
        _links(),
        _links(("a", "a", 80)),
        _links(("a", "b", 0)),
        _links(("a", "b", 80), ("a", "b", 80)),
        _links(("a", "b", 80), scenarios=[{"id": "x"}]),
        _links(("a", "b", 80), duration="3s"),
        _links(("a", "b", 80), failures=1),
        _links(("a", "b", 80), interval="1ms"),
    ],
)
def test_link_probe_workload_rejects_unsafe_declarations(text: str) -> None:
    with pytest.raises(ValidationError):
        _parse(text)


def test_links_belong_only_to_link_probe_workloads() -> None:
    document = yaml.safe_load(_links(("a", "b", 80)))
    document.update(purpose="health-probe", scenarios=[{"id": "home"}])
    with pytest.raises(ValidationError):
        TrafficWorkload.model_validate(document)
    with pytest.raises(ValidationError):
        TrafficWorkload.model_validate({**document, "links": [], "scenarios": []})


def test_judge_instruction_is_generic_and_derives_edges_from_the_source() -> None:
    lowered = " ".join(TRAFFIC_AUTHORING.lower().split())

    assert "link-probe" in TRAFFIC_AUTHORING
    assert "workloads/links.yaml" in TRAFFIC_AUTHORING
    assert "for each service-to-service dependency" in lowered
    assert "source declares" in lowered
    for forbidden in ("networkpolic", "network polic", "sregym", "deny-all", "calico", "recommendation"):
        assert forbidden not in lowered


def test_link_detector_source_uses_the_link_constructor_and_the_workload() -> None:
    source = _traffic_detector_source("links", link=True)

    assert "traffic.NewLinkDetector(" in source
    assert 'traffic-links"' in source
    assert '"links")' in source
    assert "traffic.NewLinkDetector(" not in _traffic_detector_source("health")
    assert "traffic.NewDetector(" in _traffic_detector_source("health")


def test_lifecycle_installs_a_link_detector_for_a_link_probe_workload(tmp_path: Path) -> None:
    files = _files(**{"workloads/links.yaml": _links(("example", "backend", 8080))})
    repository, deployer, artifact = _judged(tmp_path, files=files)

    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    assert [workload.name for workload in MemoryRepository(repository).traffic_workloads()] == [
        "health",
        "links",
        "verify",
    ]
    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    registrations = {detector.id: detector for detector in manifest.detectors}
    assert sorted(registrations) == ["health-objective", "service-endpoints", "traffic-health", "traffic-links"]
    assert registrations["traffic-links"].owner == "health_judge"
    assert [(watch.api_version, watch.kind) for watch in registrations["traffic-links"].watches] == [
        ("sdo.dev/v1alpha1", "SyntheticTraffic")
    ]
    detectors = repository / ".sdo/diagnostics/detectors/health"
    assert "traffic.NewLinkDetector(" in (detectors / "traffic-links/detector.go").read_text(encoding="utf-8")
    assert "traffic.NewDetector(" in (detectors / "traffic-health/detector.go").read_text(encoding="utf-8")


def test_judge_links_must_name_source_backed_services(tmp_path: Path) -> None:
    files = _files(**{"workloads/links.yaml": _links(("example", "ghost", 8080))})
    _, deployer, artifact = _judged(tmp_path, files=files)

    errors = _validate_health_judge_artifact(artifact, deployer=deployer, health_objective=OBJECTIVE, expected_round=1)

    assert any("Service/ghost" in error and "example -> ghost:8080" in error for error in errors)
    assert not any("Service/example" in error for error in errors)


def test_authored_link_workload_is_a_judge_owned_traffic_file() -> None:
    assert (
        AuthoredTrafficFile(path="workloads/links.yaml", content=_links(("a", "b", 80))).path == "workloads/links.yaml"
    )


def test_link_probe_code_names_no_benchmark_or_fault_specific_tokens() -> None:
    for relative in NEUTRAL_SOURCES:
        text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8").lower()
        for token in BENCHMARK_TOKENS:
            assert token not in text, (relative, token)
