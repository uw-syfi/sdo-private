from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from app_operator.lifecycle.operational_memory import (
    LifecycleError,
    _judge_assessment,
    ensure_operational_memory,
)
from app_operator.memory.sandbox import LocalSandboxRunner
from controller.builder.manifest import load_manifest

if TYPE_CHECKING:
    from pathlib import Path


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _initialize_application(repository: Path) -> None:
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Lifecycle Test")
    _git(repository, "config", "user.email", "lifecycle-test@localhost")
    manifest = repository / "deploy" / "application.yaml"
    manifest.parent.mkdir()
    manifest.write_text("kind: Deployment\nmetadata:\n  name: example\n", encoding="utf-8")
    _git(repository, "add", "deploy/application.yaml")
    _git(repository, "commit", "-q", "-m", "application source")


def test_bootstrap_creates_owned_valid_operational_memory_in_one_trusted_commit(tmp_path: Path) -> None:
    repository = tmp_path / "application"
    _initialize_application(repository)
    source_commit = _git(repository, "rev-parse", "HEAD")

    memory_commit = ensure_operational_memory(
        repository,
        application="example",
        health_objective="All user-facing requests succeed.",
    )

    assert memory_commit == _git(repository, "rev-parse", "HEAD")
    assert memory_commit != source_commit
    assert "owner: human" in (repository / ".sdo" / "goal.md").read_text(encoding="utf-8")
    assert "All user-facing requests succeed." in (repository / ".sdo" / "goal.md").read_text(encoding="utf-8")
    assert (repository / ".sdo" / "outcomes.jsonl").read_text(encoding="utf-8") == ""
    manifest = load_manifest(repository / ".sdo" / "diagnostics" / "manifest.yaml", app_root=repository)
    assert manifest.detectors[0].detector_class == "health"
    assert manifest.detectors[0].owner == "health_judge"
    detector_path = repository / ".sdo" / "diagnostics" / "detectors" / "health" / "objective" / "detector.go"
    detector_source = detector_path.read_text(encoding="utf-8")
    assert "sdo-health-judge" not in detector_source
    assert "NetworkPolicies()" in detector_source
    assert "network-policy-total-isolation" in detector_source
    assert "healthObjectiveDigest" in detector_source
    assert '"example": {}' in detector_source
    assert "isRequiredDeployment(deployment.Name)" in detector_source
    assert "policySelectsRequiredWorkload" in detector_source
    assert any(watch.kind == "ConfigMap" for watch in manifest.detectors[0].watches)
    assert any(watch.kind == "NetworkPolicy" for watch in manifest.detectors[0].watches)
    assert "func (Detector) Detect(context.Context" not in detector_source
    assert "return nil, nil" not in detector_source
    assert _git(repository, "show", "--format=", "--name-only", "HEAD").splitlines()
    committed_paths = _git(repository, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert all(path.startswith(".sdo/") for path in committed_paths)


def test_global_source_backed_objective_does_not_accidentally_select_only_user_named_resource() -> None:
    plan = _judge_assessment(
        {
            "health_objective": "All source-backed Deployments and Services remain healthy for user traffic.",
            "deployer_assessment": {
                "source_commit": "source",
                "resources": [
                    {"kind": "Deployment", "name": "frontend"},
                    {"kind": "Deployment", "name": "user"},
                    {"kind": "Service", "name": "frontend"},
                    {"kind": "Service", "name": "user"},
                ],
            },
        }
    )

    assert plan["deployment_names"] == ["frontend", "user"]
    assert plan["service_names"] == ["frontend", "user"]


def test_bootstrap_diagnostics_compile_and_test_in_the_observer_sandbox(tmp_path: Path) -> None:
    repository = tmp_path / "application"
    _initialize_application(repository)
    ensure_operational_memory(
        repository,
        application="example",
        health_objective="All user-facing requests succeed.",
    )

    result = LocalSandboxRunner(timeout_seconds=180).run(repository)

    assert result.returncode == 0, result.stderr or result.stdout


def test_lifecycle_upgrades_legacy_static_health_detector(tmp_path: Path) -> None:
    repository = tmp_path / "application"
    _initialize_application(repository)
    ensure_operational_memory(
        repository,
        application="example",
        health_objective="The application is healthy.",
    )
    detector = repository / ".sdo" / "diagnostics" / "detectors" / "health" / "objective" / "detector.go"
    detector.write_text(
        detector.read_text(encoding="utf-8") + "\n// legacy implementation marker: return nil, nil\n",
        encoding="utf-8",
    )
    _git(repository, "add", str(detector.relative_to(repository)))
    _git(repository, "commit", "-q", "-m", "simulate legacy detector")

    upgraded = ensure_operational_memory(
        repository,
        application="example",
        health_objective="The application is healthy.",
    )

    assert upgraded == _git(repository, "rev-parse", "HEAD")
    assert _git(repository, "log", "-1", "--format=%s") == "sdo: upgrade independent health judge"
    assert "return nil, nil" not in detector.read_text(encoding="utf-8")


def test_bootstrap_is_idempotent_and_refreshes_architecture_after_source_commit(tmp_path: Path) -> None:
    repository = tmp_path / "application"
    _initialize_application(repository)
    initial_memory_commit = ensure_operational_memory(
        repository,
        application="example",
        health_objective="The application is healthy.",
    )

    assert (
        ensure_operational_memory(
            repository,
            application="example",
            health_objective="The application is healthy.",
        )
        == initial_memory_commit
    )

    (repository / "deploy" / "service.yaml").write_text(
        "kind: Service\nmetadata:\n  name: example\n",
        encoding="utf-8",
    )
    _git(repository, "add", "deploy/service.yaml")
    _git(repository, "commit", "-q", "-m", "add service topology")
    source_commit = _git(repository, "rev-parse", "HEAD")

    refreshed_commit = ensure_operational_memory(
        repository,
        application="example",
        health_objective="The application is healthy.",
    )

    assert refreshed_commit != source_commit
    architecture = (repository / ".sdo" / "arch.md").read_text(encoding="utf-8")
    assert f"generated_at_commit: {source_commit}" in architecture
    assert "currently contains 2 tracked" in architecture
    assert "Deployment/example" in architecture
    assert "Service/example" in architecture
    assert "deploy/application.yaml" in architecture
    detector = repository / ".sdo" / "diagnostics" / "detectors" / "health" / "objective" / "detector.go"
    assert '"example": {}' in detector.read_text(encoding="utf-8")
    assert _git(repository, "log", "-1", "--format=%s") == "sdo: upgrade independent health judge"


def test_existing_memory_without_architecture_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "application"
    _initialize_application(repository)
    (repository / ".sdo").mkdir()

    with pytest.raises(LifecycleError, match="no arch.md"):
        ensure_operational_memory(
            repository,
            application="example",
            health_objective="The application is healthy.",
        )
