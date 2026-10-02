"""Topology-derived link probe: every source-declared Service port is dialed, whatever the judge listed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from controller.builder.manifest import load_manifest
from sdo.agent_runtime.lifecycle.agents import AuthoredTrafficFile
from sdo.agent_runtime.lifecycle.operational_memory import (
    TRAFFIC_TOPOLOGY_MAX_LINKS,
    _is_judge_traffic_path,
    _traffic_errors,
    _write_traffic_files,
    ensure_operational_memory,
    topology_link_workload,
)
from sdo.operational_memory.models import TRAFFIC_PROBER_SOURCE, TRAFFIC_TOPOLOGY_WORKLOAD, TrafficWorkload
from sdo.operational_memory.repository import MemoryRepository
from tests.unit.sdo.agent_runtime.lifecycle.test_agents import RecordingBackend, _git
from tests.unit.sdo.agent_runtime.lifecycle.test_link_reachability import _links
from tests.unit.sdo.agent_runtime.lifecycle.test_traffic_lifecycle import OBJECTIVE, _files, _judged

if TYPE_CHECKING:
    from pathlib import Path


def _service(name: str, *ports: int, namespace: str = "demo", **spec: object) -> str:
    document = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"selector": {"app": name}, "ports": [{"port": port} for port in ports], **spec},
    }
    return yaml.safe_dump(document)


def _commit_manifests(repository: Path, **files: str) -> None:
    for relative, text in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "manifests")


def _edges(workload: TrafficWorkload | None) -> list[tuple[str, int]]:
    assert workload is not None
    return [(link.target, link.port) for link in workload.links]


def test_every_source_service_port_gets_a_probed_edge(tmp_path: Path) -> None:
    repository, _, _ = _judged(tmp_path, files=_files())
    _commit_manifests(
        repository,
        **{
            "k8s/frontend.yaml": _service("frontend", 5000),
            "k8s/recommendation.yaml": _service("recommendation", 8085),
            "k8s/user.yaml": _service("user", 8086, 8087),
        },
    )

    workload = topology_link_workload(repository)

    assert workload is not None
    assert workload.name == TRAFFIC_TOPOLOGY_WORKLOAD
    assert workload.purpose == "link-probe"
    assert _edges(workload) == [
        ("example", 80),
        ("frontend", 5000),
        ("recommendation", 8085),
        ("user", 8086),
        ("user", 8087),
    ]
    assert {link.source for link in workload.links} == {TRAFFIC_PROBER_SOURCE}


def test_regression_a_judge_list_that_omits_a_named_service_is_still_covered(tmp_path: Path) -> None:
    """The cold C1 lifecycle listed only consul edges; the objective's Service had no inbound edge."""
    judge_links = _links(("frontend", "consul", 8500), ("search", "consul", 8500))
    repository, deployer, artifact = _judged(tmp_path, files=_files(**{"workloads/links.yaml": judge_links}))
    _commit_manifests(
        repository,
        **{"k8s/recommendation.yaml": _service("recommendation", 8085), "k8s/consul.yaml": _service("consul", 8500)},
    )

    ensure_operational_memory(
        repository,
        application="example",
        health_objective="Service recommendation must remain reachable.",
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    topology = next(
        workload
        for workload in MemoryRepository(repository).traffic_workloads()
        if workload.name == TRAFFIC_TOPOLOGY_WORKLOAD
    )
    assert ("recommendation", 8085) in _edges(topology)
    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    ids = {detector.id for detector in manifest.detectors}
    assert {"traffic-links", f"traffic-{TRAFFIC_TOPOLOGY_WORKLOAD}"} <= ids


@pytest.mark.parametrize(
    "document",
    [
        _service("external", 80, type="ExternalName", externalName="example.org"),
        _service("portless"),
        yaml.safe_dump({"apiVersion": "serving.knative.dev/v1", "kind": "Service", "metadata": {"name": "srv-geo"}}),
        yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "settings"}}),
    ],
)
def test_services_that_cannot_be_dialed_are_not_probed(tmp_path: Path, document: str) -> None:
    repository, _, _ = _judged(tmp_path, files=_files())
    _commit_manifests(repository, **{"k8s/other.yaml": document})

    assert _edges(topology_link_workload(repository)) == [("example", 80)]


def test_variants_declaring_the_same_service_port_are_probed_once_and_headless_services_are_kept(
    tmp_path: Path,
) -> None:
    repository, _, _ = _judged(tmp_path, files=_files())
    _commit_manifests(
        repository,
        **{
            "kubernetes/store.yaml": _service("store", 27017),
            "openshift/store.yaml": _service("store", 27017),
            "kubernetes/queue.yaml": _service("queue", 5672, clusterIP="None"),
        },
    )

    assert _edges(topology_link_workload(repository)) == [("example", 80), ("queue", 5672), ("store", 27017)]


def test_a_large_application_is_capped_deterministically(tmp_path: Path) -> None:
    repository, _, _ = _judged(tmp_path, files=_files())
    _commit_manifests(
        repository, **{f"k8s/s{index:03d}.yaml": _service(f"svc-{index:03d}", 8000 + index) for index in range(80)}
    )

    first = _edges(topology_link_workload(repository))
    second = _edges(topology_link_workload(repository))

    assert len(first) == TRAFFIC_TOPOLOGY_MAX_LINKS
    assert first == second
    assert first == sorted(first)


def test_no_services_means_no_topology_workload(tmp_path: Path) -> None:
    repository = tmp_path / "bare"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "t")
    _git(repository, "config", "user.email", "t@localhost")
    (repository / "README.md").write_text("x\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "x")

    assert topology_link_workload(repository) is None


def test_the_topology_workload_belongs_to_the_lifecycle_not_to_a_judge_round(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(tmp_path, files=_files())
    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )
    path = repository / f".sdo/diagnostics/traffic/workloads/{TRAFFIC_TOPOLOGY_WORKLOAD}.yaml"
    assert path.is_file()
    assert not _is_judge_traffic_path(path.relative_to(repository).as_posix())

    _write_traffic_files(repository, [])  # a judge round replaces the judge-owned set

    assert path.is_file()
    forged = AuthoredTrafficFile(
        path=f"workloads/{TRAFFIC_TOPOLOGY_WORKLOAD}.yaml", content=_links(("example", "example2", 80))
    )
    errors = _traffic_errors(
        [forged], RecordingBackend().run_deployer(repository=repository, application="x", correction_feedback=None)
    )
    assert any(TRAFFIC_TOPOLOGY_WORKLOAD in error or "topology-" in error for error in errors)


def test_refreshing_the_lifecycle_regenerates_the_workload_idempotently(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(tmp_path, files=_files())
    kwargs = {
        "application": "example",
        "health_objective": OBJECTIVE,
        "health_judge_artifact": artifact,
        "architecture_summary_markdown": deployer.architecture_summary_markdown,
    }
    ensure_operational_memory(repository, **kwargs)
    path = repository / f".sdo/diagnostics/traffic/workloads/{TRAFFIC_TOPOLOGY_WORKLOAD}.yaml"
    before = path.read_text(encoding="utf-8")

    ensure_operational_memory(repository, **kwargs)

    assert path.read_text(encoding="utf-8") == before
