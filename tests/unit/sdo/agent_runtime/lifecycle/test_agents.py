from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml
from agentshim.providers.codex import CodexSandboxConfig, parse_sandbox

from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    ClaudeLifecycleBackend,
    ClaudeTaskOutputs,
    CodexLifecycleBackend,
    DeployerAssessment,
    DeployerDraft,
    DeployerHandoff,
    HealthJudgeArtifact,
    HealthJudgeWorkspaceArtifact,
    LifecycleAgentError,
    TopologyResourceDTO,
    _command_escapes_repository,
)
from sdo.agent_runtime.lifecycle.operational_memory import (
    _HEALTH_DETECTOR_TEST_SOURCE,
    LifecycleError,
    _canonicalize_health_registration,
    _deployer_assessment,
    _judge_assessment,
    _render_health_detector,
    _validate_health_judge_artifact,
    _write_health_judge_authoring_context,
    check_detector_workspace,
    ensure_operational_memory,
    reuse_initial_lifecycle_if_valid,
    run_initial_lifecycle,
)
from sdo.agent_runtime.lifecycle.validation_cache import LifecycleValidationCache
from sdo.operational_memory.sandbox import LocalSandboxRunner, SandboxResult
from tests.structured_turns import ScriptedAgent, failure, fake_codex_login, reply, turn_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentshim import CommandRequest
    from agentshim.testing import FakeRun


@pytest.fixture(autouse=True)
def _codex_login(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The Codex health judge's workspace-write turns copy a Codex login."""
    with fake_codex_login(monkeypatch):
        yield


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path) -> Path:
    repository = root / "application"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Lifecycle Agent Test")
    _git(repository, "config", "user.email", "lifecycle-agent-test@localhost")
    manifest = repository / "deploy.yaml"
    manifest.write_text(
        """apiVersion: apps/v1
kind: Deployment
metadata: {name: example, namespace: demo}
spec:
  selector: {matchLabels: {app: example}}
  template:
    metadata: {labels: {app: example}}
    spec:
      containers: [{name: example, image: example:v1}]
---
apiVersion: v1
kind: Service
metadata: {name: example, namespace: demo}
spec: {selector: {app: example}, ports: [{port: 80}]}
""",
        encoding="utf-8",
    )
    _git(repository, "add", "deploy.yaml")
    _git(repository, "commit", "-q", "-m", "application source")
    return repository


def _artifact(
    repository: Path,
    *,
    session_id: str,
    round_index: int,
    deployer: DeployerAssessment,
) -> HealthJudgeArtifact:
    objective = "Deployment example and Service example must remain available."
    plan = _judge_assessment(
        {
            "health_objective": objective,
            "deployer_assessment": deployer.model_dump(mode="json"),
        }
    )
    return HealthJudgeArtifact(
        session_id=session_id,
        round=round_index,
        objective_digest=hashlib.sha256(objective.encode()).hexdigest(),
        source_commit=_git(repository, "rev-parse", "HEAD"),
        covered_resources=[
            TopologyResourceDTO.model_validate(resource) for resource in deployer.model_dump(mode="json")["resources"]
        ],
        failure_patterns=["unavailable Deployment", "Service without selected ready pods"],
        detector_source=_render_health_detector(plan),
        detector_test_source=_HEALTH_DETECTOR_TEST_SOURCE,
    )


class RecordingBackend:
    def __init__(self) -> None:
        self.deployer_calls: list[str | None] = []
        self.judge_calls: list[tuple[int, str | None, str | None]] = []

    def run_deployer(
        self,
        *,
        repository: Path,
        application: str,
        correction_feedback: str | None,
    ) -> DeployerAssessment:
        self.deployer_calls.append(correction_feedback)
        raw = _deployer_assessment({"repository": str(repository), "application": application})
        return DeployerAssessment(
            session_id=f"deployer-{len(self.deployer_calls)}",
            source_commit=str(raw["source_commit"]),
            topology_fingerprint=str(raw["topology_fingerprint"]),
            resources=[TopologyResourceDTO.model_validate(resource) for resource in raw["resources"]],
            architecture_summary_markdown=(
                "# Architecture\n\nThe example Deployment serves traffic through the example Service."
            ),
        )

    def run_health_judge(
        self,
        *,
        repository: Path,
        application: str,
        health_objective: str,
        deployer: DeployerAssessment,
        round_index: int,
        previous: HealthJudgeArtifact | None,
        correction_feedback: str | None,
        active_resources: list[ActiveTopologyResourceDTO] | None = None,
    ) -> HealthJudgeArtifact:
        del active_resources, application, health_objective
        self.judge_calls.append((round_index, previous.session_id if previous else None, correction_feedback))
        return _artifact(
            repository,
            session_id=f"judge-call-{len(self.judge_calls)}",
            round_index=round_index,
            deployer=deployer,
        )


class PassingValidator:
    def __init__(self) -> None:
        self.runs: list[Path] = []

    def run(self, app_root: Path) -> SandboxResult:
        self.runs.append(app_root)
        assert (app_root / ".sdo/diagnostics/detectors/health/objective/detector.go").is_file()
        return SandboxResult(returncode=0, stdout="compiled")


class IdentifiedPassingValidator(PassingValidator):
    def __init__(self, identity: str = "validator-image@sha256:trusted") -> None:
        super().__init__()
        self.identity = identity

    def validation_identity(self) -> str:
        return self.identity


def test_initial_lifecycle_uses_fresh_structured_agents_and_three_bounded_judge_rounds(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = RecordingBackend()
    validator = PassingValidator()

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend,
        validator=validator,
        judge_rounds=3,
    )

    assert len(backend.deployer_calls) == 1
    assert "Trusted controller-derived source facts" in str(backend.deployer_calls[0])
    assert "architecture_summary_markdown must mention every resource name verbatim" in str(backend.deployer_calls[0])
    assert [call[0] for call in backend.judge_calls] == [1, 2, 3]
    assert backend.judge_calls[0][1] is None
    assert backend.judge_calls[1][1] == "judge-call-1"
    assert backend.judge_calls[2][1] == "judge-call-2"
    assert len(validator.runs) == 3
    provenance = json.loads(
        json.dumps(__import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text()))
    )
    session_ids = [provenance["deployer"]["session_id"]]
    session_ids.extend(round_["session_id"] for round_ in provenance["health_judge_rounds"])
    assert session_ids == ["deployer-1", "judge-call-1", "judge-call-2", "judge-call-3"]
    assert len(set(session_ids)) == 4
    assert "The example Deployment serves traffic" in (repository / ".sdo/arch.md").read_text()
    detector_source = (repository / ".sdo/diagnostics/detectors/health/objective/detector.go").read_text(
        encoding="utf-8"
    )
    assert '{APIVersion: "v1", Kind: "ConfigMap"}' in detector_source


def test_controller_attaches_deterministic_inventory_to_the_deployer_handoff(tmp_path: Path) -> None:
    """The deployer summarizes; it does not transcribe the controller-derived inventory.

    Codex deployers repeatedly returned ``resources: []`` for a 136-resource inventory
    and spent a whole retry session copying it back verbatim.
    """
    repository = _repository(tmp_path)

    class SummaryOnlyBackend(RecordingBackend):
        def run_deployer(
            self,
            *,
            repository: Path,
            application: str,
            correction_feedback: str | None,
        ) -> DeployerHandoff:
            self.deployer_calls.append(correction_feedback)
            raw = _deployer_assessment({"repository": str(repository), "application": application})
            return DeployerHandoff(
                session_id=f"deployer-{len(self.deployer_calls)}",
                source_commit=str(raw["source_commit"]),
                topology_fingerprint=str(raw["topology_fingerprint"]),
                architecture_summary_markdown=(
                    "# Architecture\n\nThe example Deployment serves traffic through the example Service."
                ),
            )

    backend = SummaryOnlyBackend()

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend,
        validator=PassingValidator(),
    )

    assert len(backend.deployer_calls) == 1
    assert "controller attaches the resource inventory" in str(backend.deployer_calls[0])
    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    expected = _deployer_assessment({"repository": str(repository), "application": "example"})
    assert provenance["deployer"]["resources"] == expected["resources"]
    assert provenance["deployer"]["session_id"] == "deployer-1"


def test_deployer_output_schema_omits_the_controller_owned_inventory() -> None:
    assert "resources" not in DeployerDraft.model_json_schema()["properties"]
    assert "resources" in DeployerAssessment.model_json_schema()["properties"]


def test_initial_lifecycle_allows_judge_to_edit_and_self_check_an_isolated_workspace(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."

    class WorkspaceBackend(RecordingBackend):
        def run_health_judge_workspace(
            self,
            *,
            repository: Path,
            application: str,
            health_objective: str,
            deployer: DeployerAssessment,
            round_index: int,
            previous: HealthJudgeArtifact | None,
            correction_feedback: str | None,
            active_resources: list[ActiveTopologyResourceDTO] | None = None,
        ) -> HealthJudgeWorkspaceArtifact:
            del application, health_objective, previous, correction_feedback, active_resources
            assert (repository / ".sdo/diagnostics/manifest.yaml").is_file()
            authored = _artifact(
                repository,
                session_id=f"workspace-{round_index}",
                round_index=round_index,
                deployer=deployer,
            )
            detector = repository / ".sdo/diagnostics/detectors/health/objective/detector.go"
            detector.write_text(authored.detector_source + "\n// edited in workspace\n", encoding="utf-8")
            test = repository / ".sdo/diagnostics/detectors/health/objective/detector_test.go"
            test.write_text(authored.detector_test_source, encoding="utf-8")
            return HealthJudgeWorkspaceArtifact(
                session_id=authored.session_id,
                round=round_index,
                objective_digest=authored.objective_digest,
                source_commit=deployer.source_commit,
                covered_resources=authored.covered_resources,
                failure_patterns=authored.failure_patterns,
            )

        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            raise AssertionError("structured source fallback should not run")

    validator = PassingValidator()
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=WorkspaceBackend(),
        validator=validator,
        judge_rounds=1,
    )

    assert len(validator.runs) == 1
    assert "edited in workspace" in (repository / ".sdo/diagnostics/detectors/health/objective/detector.go").read_text(
        encoding="utf-8"
    )


def test_authoring_check_returns_semantic_feedback_before_starting_compiler(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = RecordingBackend()
    deployer = backend.run_deployer(repository=repository, application="example", correction_feedback=None)
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    objective = "Deployment example and Service example must remain available."
    ensure_operational_memory(
        repository,
        application="example",
        health_objective=objective,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )
    _write_health_judge_authoring_context(
        repository,
        deployer=deployer,
        health_objective=objective,
        round_index=1,
        active_resources=None,
    )
    source = repository / ".sdo/diagnostics/detectors/health/objective/detector.go"
    source.write_text(source.read_text(encoding="utf-8").replace(artifact.objective_digest, "0" * 64))

    class CompilerMustNotRun:
        def run(self, _app_root: Path) -> SandboxResult:
            raise AssertionError("semantic failures should be returned before compilation")

    result = check_detector_workspace(repository, validator=CompilerMustNotRun())

    assert result.returncode == 1
    assert "exact objective digest" in result.stderr


def test_health_judge_rejects_source_variants_outside_active_topology(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    resources = [TopologyResourceDTO.model_validate(resource) for resource in raw["resources"]]
    resources.append(
        TopologyResourceDTO(
            kind="Deployment",
            name="example-knative",
            namespace="demo",
            source="knative.yaml",
            dependencies=[],
        )
    )
    deployer = DeployerAssessment(
        session_id="deployer",
        source_commit=str(raw["source_commit"]),
        topology_fingerprint=str(raw["topology_fingerprint"]),
        resources=resources,
        architecture_summary_markdown="# Architecture\n\nexample and example-knative deployments with example service.",
    )
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)

    errors = _validate_health_judge_artifact(
        artifact,
        deployer=deployer,
        health_objective="Deployment example and Service example must remain available.",
        expected_round=1,
        active_resources=[
            ActiveTopologyResourceDTO(kind="Deployment", name="example"),
            ActiveTopologyResourceDTO(kind="Service", name="example"),
        ],
    )

    assert any("inactive source variants" in error and "Deployment/example-knative" in error for error in errors)


def test_lifecycle_canonicalizes_model_coverage_from_active_topology(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    class InventedCoverageBackend(RecordingBackend):
        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            artifact = super().run_health_judge(**kwargs)  # type: ignore[arg-type]
            return artifact.model_copy(
                update={
                    "covered_resources": [
                        *artifact.covered_resources,
                        TopologyResourceDTO(
                            kind="ConfigMap",
                            name="runtime-generated",
                            namespace="default",
                            source="deploy.yaml",
                            dependencies=[],
                        ),
                    ]
                }
            )

    active = [
        ActiveTopologyResourceDTO(kind="Deployment", name="example"),
        ActiveTopologyResourceDTO(kind="Service", name="example"),
    ]
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        active_resources=active,
        backend=InventedCoverageBackend(),
        validator=PassingValidator(),
        judge_rounds=1,
    )

    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    assert [(resource["kind"], resource["name"]) for resource in provenance["health_judge"]["covered_resources"]] == [
        ("Deployment", "example"),
        ("Service", "example"),
    ]


def test_lifecycle_canonicalizes_global_objective_coverage_without_active_topology(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    class OmittedCoverageBackend(RecordingBackend):
        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            artifact = super().run_health_judge(**kwargs)  # type: ignore[arg-type]
            objective = str(kwargs["health_objective"])
            digest = hashlib.sha256(objective.encode()).hexdigest()
            return artifact.model_copy(
                update={
                    "covered_resources": [],
                    "objective_digest": digest,
                    "detector_source": artifact.detector_source.replace(artifact.objective_digest, digest),
                }
            )

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=(
            "All source-backed Deployments remain available, all selected Services have ready endpoints, "
            "and representative requests succeed."
        ),
        backend=OmittedCoverageBackend(),
        validator=PassingValidator(),
        judge_rounds=1,
    )

    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    assert [(resource["kind"], resource["name"]) for resource in provenance["health_judge"]["covered_resources"]] == [
        ("Deployment", "example"),
        ("Service", "example"),
    ]


def test_existing_model_backed_lifecycle_is_reused_only_while_source_topology_matches(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=PassingValidator(),
        judge_rounds=3,
    )

    reuse_validator = PassingValidator()
    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=reuse_validator,
    )
    assert len(reuse_validator.runs) == 1

    manifest = repository / "deploy.yaml"
    manifest.write_text(manifest.read_text().replace("example:v1", "example:v2"), encoding="utf-8")
    _git(repository, "add", "deploy.yaml")
    _git(repository, "commit", "-q", "-m", "change source topology")
    assert not reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=PassingValidator(),
    )


_CONFIGMAP_MANIFEST = """apiVersion: v1
kind: ConfigMap
metadata: {name: example-script, namespace: demo}
data: {init.sh: "echo ok"}
"""


def _commit_source(repository: Path, name: str, content: str, *, broker: bool) -> None:
    (repository / name).write_text(content, encoding="utf-8")
    _git(repository, "add", name)
    if broker:
        _git(
            repository,
            "-c",
            "user.name=SDO Commit Broker",
            "-c",
            "user.email=sdo-commit-broker@localhost",
            "commit",
            "-q",
            "-m",
            "sdo(incident-1): validated operational memory\n\n"
            "SDO-Incident: incident-1\nSDO-Actor: responder\nSDO-Phase: outcome\nSDO-Validation: passed",
        )
    else:
        _git(repository, "commit", "-q", "-m", "operator source change")


def _lifecycle_repository(tmp_path: Path, objective: str) -> Path:
    repository = _repository(tmp_path)
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=PassingValidator(),
        judge_rounds=3,
    )
    return repository


def test_lifecycle_is_reused_after_validated_sdo_source_change_that_keeps_judged_topology(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    repository = _lifecycle_repository(tmp_path, objective)
    _commit_source(repository, "configmap.yaml", _CONFIGMAP_MANIFEST, broker=True)

    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=PassingValidator(),
    )
    assert _git(repository, "worktree", "list").count("\n") == 0


def test_lifecycle_is_not_reused_after_unvalidated_source_change(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    repository = _lifecycle_repository(tmp_path, objective)
    _commit_source(repository, "configmap.yaml", _CONFIGMAP_MANIFEST, broker=False)

    assert not reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=PassingValidator(),
    )


def test_lifecycle_is_not_reused_after_validated_sdo_change_to_judged_topology(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    repository = _lifecycle_repository(tmp_path, objective)
    relabeled = (repository / "deploy.yaml").read_text(encoding="utf-8").replace("app: example", "app: example-v2")
    _commit_source(repository, "deploy.yaml", relabeled, broker=True)

    assert not reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=PassingValidator(),
    )


def test_lifecycle_reuse_skips_identical_independent_validation_with_attestation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."
    validator = IdentifiedPassingValidator()
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=validator,
        judge_rounds=3,
    )
    assert len(validator.runs) == 3

    reuse_validator = IdentifiedPassingValidator()
    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=reuse_validator,
    )
    assert reuse_validator.runs == []


def test_lifecycle_attestation_is_invalidated_by_validator_or_diagnostics_change(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=IdentifiedPassingValidator(),
        judge_rounds=3,
    )

    changed_validator = IdentifiedPassingValidator("validator-image@sha256:new")
    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=changed_validator,
    )
    assert len(changed_validator.runs) == 1
    repeated_changed_validator = IdentifiedPassingValidator("validator-image@sha256:new")
    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=repeated_changed_validator,
    )
    assert repeated_changed_validator.runs == []

    detector = repository / ".sdo/diagnostics/detectors/health/objective/detector.go"
    detector.write_text(detector.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
    changed_detector_validator = IdentifiedPassingValidator()
    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        validator=changed_detector_validator,
    )
    assert len(changed_detector_validator.runs) == 1


def test_lifecycle_reuse_requires_the_same_active_topology(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."
    active = [
        ActiveTopologyResourceDTO(kind="Deployment", name="example"),
        ActiveTopologyResourceDTO(kind="Service", name="example"),
    ]
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        active_resources=active,
        backend=RecordingBackend(),
        validator=PassingValidator(),
        judge_rounds=3,
    )

    assert reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        active_resources=active,
        validator=PassingValidator(),
    )
    assert not reuse_initial_lifecycle_if_valid(
        repository,
        application="example",
        health_objective=objective,
        active_resources=[ActiveTopologyResourceDTO(kind="Deployment", name="example")],
        validator=PassingValidator(),
    )


def test_evolved_source_atomically_refreshes_model_memory_and_preserves_outcomes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    objective = "Deployment example and Service example must remain available."
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=PassingValidator(),
        judge_rounds=3,
    )
    outcomes = repository / ".sdo" / "outcomes.jsonl"
    outcomes.write_text('{"incident_id":"prior"}\n', encoding="utf-8")
    diagnostics_manifest = repository / ".sdo" / "diagnostics" / "manifest.yaml"
    diagnostics_manifest.write_text(
        diagnostics_manifest.read_text(encoding="utf-8").replace(
            "      - apiVersion: v1\n        kind: ConfigMap\n",
            "",
        ),
        encoding="utf-8",
    )
    manifest = repository / "deploy.yaml"
    manifest.write_text(manifest.read_text().replace("example:v1", "example:v2"), encoding="utf-8")
    _git(repository, "add", ".sdo/outcomes.jsonl", ".sdo/diagnostics/manifest.yaml", "deploy.yaml")
    _git(repository, "commit", "-q", "-m", "evolve source after prior incident")
    source_commit = _git(repository, "rev-parse", "HEAD")

    refreshed_backend = RecordingBackend()
    refreshed_commit = run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=refreshed_backend,
        validator=PassingValidator(),
        judge_rounds=3,
    )

    assert refreshed_commit != source_commit
    assert _git(repository, "log", "-1", "--format=%s") == "sdo: refresh model-backed operational memory"
    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    assert provenance["deployer"]["session_id"] == "deployer-1"
    assert [item["session_id"] for item in provenance["health_judge_rounds"]] == [
        "judge-call-1",
        "judge-call-2",
        "judge-call-3",
    ]
    assert provenance["deployer"]["source_commit"] == source_commit
    assert outcomes.read_text(encoding="utf-8") == '{"incident_id":"prior"}\n'
    assert "kind: ConfigMap" in diagnostics_manifest.read_text(encoding="utf-8")
    committed_paths = _git(repository, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert ".sdo/arch.md" in committed_paths
    assert ".sdo/lifecycle-provenance.yaml" in committed_paths


def test_lifecycle_rejects_benchmark_or_environment_oracles_before_compilation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    class OracleBackend(RecordingBackend):
        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            artifact = super().run_health_judge(**kwargs)  # type: ignore[arg-type]
            return artifact.model_copy(
                update={"detector_source": artifact.detector_source + "\n// read SREGYM_VERDICT_PATH\n"}
            )

    with pytest.raises(LifecycleError, match="benchmark|oracle"):
        run_initial_lifecycle(
            repository,
            application="example",
            health_objective="Deployment example and Service example must remain available.",
            backend=OracleBackend(),
            validator=PassingValidator(),
            judge_rounds=1,
        )

    assert not (repository / ".sdo").exists()


def test_lifecycle_retries_failed_validation_within_the_same_judge_round(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path)
    backend = RecordingBackend()

    class TwoFailureValidator(PassingValidator):
        def run(self, app_root: Path) -> SandboxResult:
            result = super().run(app_root)
            if len(self.runs) <= 2:
                return SandboxResult(returncode=1, stderr="registration contract mismatch")
            return result

    validator = TwoFailureValidator()

    with caplog.at_level(logging.WARNING, logger="sdo.agent_runtime.lifecycle.operational_memory"):
        run_initial_lifecycle(
            repository,
            application="example",
            health_objective="Deployment example and Service example must remain available.",
            backend=backend,
            validator=validator,
            judge_rounds=3,
            judge_corrections_per_round=3,
        )

    assert [call[0] for call in backend.judge_calls] == [1, 1, 1, 2, 3]
    assert "registration contract mismatch" in str(backend.judge_calls[1][2])
    assert "registration contract mismatch" in str(backend.judge_calls[2][2])
    assert len(validator.runs) == 5
    assert caplog.messages[:2] == [
        "health judge round 1 attempt 1 failed validation: registration contract mismatch",
        "health judge round 1 attempt 2 failed validation: registration contract mismatch",
    ]


def test_lifecycle_correction_feedback_keeps_go_test_failures_next_to_toolchain_noise(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = RecordingBackend()
    violation = (
        'detector "health-objective" reported ExternalName Service sdo-externalname-check/jaeger '
        "only when it had no ready endpoints or pods"
    )

    class GoTestFailureValidator(PassingValidator):
        def run(self, app_root: Path) -> SandboxResult:
            result = super().run(app_root)
            if len(self.runs) == 1:
                return SandboxResult(
                    returncode=1,
                    stdout=f"--- FAIL: TestHealthDetectorsExemptExternalNameServices\n    {violation}\n",
                    stderr="go: downloading k8s.io/api v0.30.3\n",
                )
            return result

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend,
        validator=GoTestFailureValidator(),
        judge_rounds=1,
        judge_corrections_per_round=2,
    )

    feedback = str(backend.judge_calls[1][2])
    assert violation in feedback
    assert "go: downloading" in feedback


def test_lifecycle_enforces_immutable_health_registration_around_judge_authored_checks(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    class WrongRegistrationBackend(RecordingBackend):
        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            artifact = super().run_health_judge(**kwargs)  # type: ignore[arg-type]
            wrong_source = (
                artifact.detector_source.replace('ID: "health-objective"', 'ID: "judge-chosen"', 1)
                .replace("Class: sdk.DetectorClassHealth", "Class: sdk.DetectorClassIncident")
                .replace("Owner: sdk.DetectorOwnerHealthJudge", "Owner: sdk.DetectorOwnerResponder")
                .replace('OriginatingCommit: "lifecycle-bootstrap"', 'OriginatingCommit: "judge-chosen"')
            )
            return artifact.model_copy(update={"detector_source": wrong_source})

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=WrongRegistrationBackend(),
        validator=PassingValidator(),
        judge_rounds=3,
    )

    source = (repository / ".sdo/diagnostics/detectors/health/objective/detector.go").read_text()
    assert 'ID: "health-objective"' in source
    assert "Class: sdk.DetectorClassHealth" in source
    assert "Owner: sdk.DetectorOwnerHealthJudge" in source
    assert 'OriginatingCommit: "lifecycle-bootstrap"' in source
    assert "judge-chosen" not in source


def test_canonical_health_registration_prunes_alias_import_used_only_by_replaced_spec() -> None:
    source = """package objective

import (
    \"time\"
    appsv1 \"k8s.io/api/apps/v1\"
    corev1 \"k8s.io/api/core/v1\"
    \"k8s.io/apimachinery/pkg/runtime/schema\"
    \"sdo.dev/controller/sdk\"
)

func New() sdk.Detector { return Detector{} }
type Detector struct{}
func (Detector) Spec() sdk.DetectorSpec {
    _ = appsv1.SchemeGroupVersion
    _ = schema.GroupVersionKind{}
    return sdk.DetectorSpec{}
}
func (Detector) Detect(ctx context.Context, snap sdk.DetectionContext) ([]sdk.Finding, error) {
    _ = corev1.ConditionTrue
    return nil, nil
}
func apiVersionForKind(kind string) string {
    if kind == "Deployment" {
        return "apps/v1"
    }
    return "v1"
}
"""

    canonical = _canonicalize_health_registration(source)

    assert 'appsv1 "k8s.io/api/apps/v1"' not in canonical
    assert '"k8s.io/apimachinery/pkg/runtime/schema"' not in canonical
    assert 'corev1 "k8s.io/api/core/v1"' in canonical
    assert 'return "apps/v1"' in canonical
    assert 'return "v1"' in canonical


def test_lifecycle_rejects_source_default_as_a_literal_runtime_namespace(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manifest = repository / "deploy.yaml"
    manifest.write_text(manifest.read_text().replace(", namespace: demo", ""))
    _git(repository, "add", "deploy.yaml")
    _git(repository, "commit", "-q", "-m", "use runtime-selected namespace")

    class NamespaceCorrectionBackend(RecordingBackend):
        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            artifact = super().run_health_judge(**kwargs)  # type: ignore[arg-type]
            if len(self.judge_calls) == 1:
                source = artifact.detector_source.replace(
                    "const deterministicHealthDetectorVersion",
                    'const sourceNamespace = "default"\nconst deterministicHealthDetectorVersion',
                )
                return artifact.model_copy(update={"detector_source": source})
            return artifact

    backend = NamespaceCorrectionBackend()
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend,
        validator=PassingValidator(),
        judge_rounds=3,
    )

    assert [call[0] for call in backend.judge_calls] == [1, 1, 2, 3]
    assert "hard-codes source namespace default" in str(backend.judge_calls[1][2])


def test_codex_backend_starts_independent_read_only_sessions_and_validates_structured_outputs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})

    schemas: list[dict[str, object]] = []

    def respond(request: CommandRequest) -> FakeRun:
        schema = turn_schema(request)
        schemas.append(schema)
        if "architecture_summary_markdown" in schema["properties"]:
            output = {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
            }
        else:
            deployer = DeployerAssessment(
                session_id="deployer-session",
                source_commit=str(raw["source_commit"]),
                topology_fingerprint=str(raw["topology_fingerprint"]),
                resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
                architecture_summary_markdown="# Architecture\n\nExample Deployment and Service.",
            )
            artifact = _artifact(repository, session_id="placeholder", round_index=1, deployer=deployer)
            output = artifact.model_dump(mode="json", exclude={"session_id"})
        return reply("codex", output, session_id=f"fresh-session-{len(agent.requests)}")

    agent = ScriptedAgent(respond)
    backend = CodexLifecycleBackend(executor=agent.executor)
    deployer = backend.run_deployer(
        repository=repository,
        application="example",
        correction_feedback=None,
    )
    judge = backend.run_health_judge(
        repository=repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        deployer=DeployerAssessment(
            **deployer.model_dump(),
            resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
        ),
        round_index=1,
        previous=None,
        correction_feedback=None,
    )

    assert deployer.session_id == "fresh-session-1"
    assert judge.session_id == "fresh-session-2"
    prompts = agent.prompts
    assert len(agent.argvs) == 2
    assert all("resume" not in argv for argv in agent.argvs)
    assert all(parse_sandbox(argv) == CodexSandboxConfig(mode="read-only") for argv in agent.argvs)
    assert all("--output-schema" in argv and "--json" in argv for argv in agent.argvs)
    assert "resources" not in cast("dict[str, object]", schemas[0]["properties"])
    assert "controller attaches the deterministic inventory" in prompts[0].replace("\n", " ")
    assert "copy every resource required by the objective exactly from the deployer handoff" in prompts[1]
    assert "Trusted controller/sdk API reference" in prompts[1]
    assert "Inspect only the current application checkout" in prompts[1]
    assert "Do not create or execute helper scripts in temporary directories" in prompts[0]
    assert "Do not use `$TMPDIR` or `/tmp`" in prompts[0]
    assert (
        "Authoritative objective SHA-256: 1e71839b3094cb9c80bc60d0fa0186c30746b677eb96b7cb094de4e1140401cb"
    ) in prompts[1]
    assert "last structured response is the only response the controller accepts" in prompts[1]
    assert "Every Go func declaration must be package-level" in prompts[1]
    assert "Mentally parse both complete files before returning them" in prompts[1].replace("\n", " ")
    # No fault-class hint: the judge derives its checks from the objective and the topology.
    assert "missing-configmap" not in prompts[1].lower()
    assert "imperatively" not in prompts[1]


@pytest.mark.parametrize(
    "escaped_command",
    [
        "find ../older-run -name detector.go",
        "sed -n 1,80p {outside}/detector.go",
        "cat /proc/self/environ",
    ],
)
def test_codex_backend_rejects_sessions_that_read_outside_application_repository(
    tmp_path: Path,
    escaped_command: str,
) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    escaped_command = escaped_command.format(outside=tmp_path.parent / "older-run")

    agent = ScriptedAgent(
        lambda _request: reply(
            "codex",
            {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
            },
            session_id="fresh-session",
            commands=[escaped_command],
        )
    )
    backend = CodexLifecycleBackend(executor=agent.executor)

    with pytest.raises(LifecycleAgentError, match="outside the application repository"):
        backend.run_deployer(
            repository=repository,
            application="example",
            correction_feedback=None,
        )


@pytest.mark.parametrize(
    "command",
    [
        "cat > /tmp/objective.txt << 'EOF'\nobjective\nEOF",
        "cat > \"$TMPDIR/covered_resources.json\" << 'EOF'\n[]\nEOF",
        "git show ed44ea9:/.sdo/diagnostics/detectors/health/objective/detector_test.go | head -50",
        'find . -name "*deployment*.yaml" -path "*/kubernetes/*"',
    ],
)
def test_repository_audit_allows_safe_non_external_paths(tmp_path: Path, command: str) -> None:
    repository = _repository(tmp_path)

    assert not _command_escapes_repository(command, repository)


# Health-judge commands from a live lifecycle (2026-09-28): route strings inside quoted
# search patterns are regex alternatives, not filesystem paths.
_ROUTE_PATTERN_COMMANDS = [
    """/bin/bash -lc "rg --files | rg '("'^|/)(frontend|main|server|handler|route|.*'"\\\\.go"'$)'"' | head -100 && """
    """rg -n 'HandleFunc|/hotels|/recommendations' services\"""",
    """/bin/bash -lc "ls -la .sdo/diagnostics/detectors/health/objective; """
    """sed -n '1,240p' services/frontend/frontend.go; """
    """rg -n 'HandleFunc|http\\\\.Handle|ListenAndServe|/hotels|/recommendations|/user|/reservation|/search' """
    """services/frontend cmd\"""",
    """/bin/bash -lc "sed -n '1,280p' services/frontend/server.go && """
    """rg -n 'inDate|outDate|/hotels|HandleFunc|ServeMux|Post\\\\(|GET\\\\(' services\"""",
    'grep -nE "(/hotels|/user)" services/frontend/server.go',
]


@pytest.mark.parametrize("command", _ROUTE_PATTERN_COMMANDS)
def test_repository_audit_allows_route_alternatives_in_quoted_search_patterns(tmp_path: Path, command: str) -> None:
    repository = _repository(tmp_path)

    assert not _command_escapes_repository(command, repository)


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("""/bin/bash -lc "rg -n 'a|/hotels' /etc\"""", id="unquoted-path-after-pattern"),
        pytest.param("""/bin/bash -lc "rg -n HandleFunc services|/opt/tool\"""", id="unquoted-pipe-to-absolute"),
        pytest.param("""/bin/bash -lc "cat '/etc/passwd'\"""", id="quoted-path"),
        pytest.param("""/bin/bash -lc "cat /etc/passwd\"""", id="wrapped-path"),
        pytest.param("grep -n 'x' '/etc/hosts'", id="quoted-path-argument"),
    ],
)
def test_repository_audit_still_rejects_external_paths_near_search_patterns(tmp_path: Path, command: str) -> None:
    repository = _repository(tmp_path)

    assert _command_escapes_repository(command, repository)


def test_repository_audit_still_rejects_external_path_after_git_object_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    assert _command_escapes_repository(
        "git show ed44ea9:/.sdo/goal.md; cat /etc/passwd",
        repository,
    )


@pytest.mark.parametrize(
    "path",
    ["{repo}/../../etc/passwd", "{repo}/sub/../../outside.txt", "/usr/bin/../../etc/shadow"],
)
def test_repository_audit_rejects_parent_segments_inside_absolute_paths(tmp_path: Path, path: str) -> None:
    """A path that starts inside an allowed root can still climb out of it."""
    repository = _repository(tmp_path)

    assert _command_escapes_repository(f"cat {path.format(repo=repository)}", repository)


_CLAUDE_SESSION = "0931c4c1-3ff3-4137-b72d-a1993ddb2cd3"
_CLAUDE_TASK_OUTPUT = f"/tmp/claude-1000/-tmp-sdo-lifecycle-application/{_CLAUDE_SESSION}/tasks/b7k2x9q1.output"


def _claude_task_outputs(session_id: str = _CLAUDE_SESSION) -> ClaudeTaskOutputs:
    return ClaudeTaskOutputs.for_session(session_id, environ={}, uid=1000)


@pytest.mark.parametrize(
    ("environ", "uid", "expected_root"),
    [
        ({}, 1000, "/tmp/claude-1000"),
        ({"TMPDIR": "/var/tmp/"}, 1001, "/var/tmp/claude-1001"),
        ({"TMPDIR": "/var/tmp", "CLAUDE_CODE_TMPDIR": "/scratch/cc"}, 0, "/scratch/cc/claude-0"),
    ],
)
def test_claude_task_output_root_follows_claude_code_tmpdir_resolution(
    environ: dict[str, str], uid: int, expected_root: str
) -> None:
    outputs = ClaudeTaskOutputs.for_session(_CLAUDE_SESSION, environ=environ, uid=uid)

    assert outputs.root == Path(expected_root)


def test_repository_audit_allows_claude_reading_its_own_background_task_output(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    assert _command_escapes_repository(f"cat {_CLAUDE_TASK_OUTPUT}", repository)
    assert not _command_escapes_repository(
        f"cat {_CLAUDE_TASK_OUTPUT}", repository, task_outputs=_claude_task_outputs()
    )
    assert not _command_escapes_repository(
        f"tail -n 50 '{_CLAUDE_TASK_OUTPUT}' | grep -i error", repository, task_outputs=_claude_task_outputs()
    )


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("cat /tmp/objective.txt", id="other-tmp-file"),
        pytest.param("ls /tmp/claude-1000", id="agent-private-root"),
        pytest.param(f"cat /tmp/claude-1000/-tmp-sdo-lifecycle-application/{_CLAUDE_SESSION}/x.jsonl", id="not-tasks"),
        pytest.param(_CLAUDE_TASK_OUTPUT.replace("claude-1000", "claude-1001"), id="other-uid"),
        pytest.param(
            _CLAUDE_TASK_OUTPUT.replace(_CLAUDE_SESSION, "11111111-2222-3333-4444-555555555555"), id="other-session"
        ),
        pytest.param(_CLAUDE_TASK_OUTPUT.replace("b7k2x9q1.output", "b7k2x9q1.log"), id="not-output"),
        pytest.param(_CLAUDE_TASK_OUTPUT.replace("b7k2x9q1", "*"), id="glob-task"),
        pytest.param(_CLAUDE_TASK_OUTPUT.replace("-tmp-sdo-lifecycle-application", "*"), id="glob-project"),
        pytest.param(_CLAUDE_TASK_OUTPUT.replace("b7k2x9q1", "$TASK"), id="variable-task"),
        pytest.param(f"cat {_CLAUDE_TASK_OUTPUT}/../../../../../etc/passwd", id="climb-from-output"),
        pytest.param(
            f"cat /tmp/claude-1000/-tmp-sdo-lifecycle-application/{_CLAUDE_SESSION}/tasks/../../x/tasks/a.output",
            id="climb-within-root",
        ),
        pytest.param(f"cat {_CLAUDE_TASK_OUTPUT} && cat /etc/passwd", id="compound-outside-read"),
        pytest.param(f"cat /etc/passwd; tail {_CLAUDE_TASK_OUTPUT}", id="compound-outside-read-first"),
    ],
)
def test_repository_audit_task_output_allowance_is_narrow(tmp_path: Path, command: str) -> None:
    repository = _repository(tmp_path)

    assert _command_escapes_repository(
        command if " " in command else f"cat {command}", repository, task_outputs=_claude_task_outputs()
    )


def test_claude_backend_accepts_session_that_reads_its_own_background_task_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    claude_tmpdir = tmp_path / "claude-tmp"
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(claude_tmpdir))
    task_output = claude_tmpdir / f"claude-{os.getuid()}" / "-app" / "claude-session" / "tasks" / "bq1.output"
    agent = ScriptedAgent(
        lambda _request: reply(
            "claude",
            {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
            },
            session_id="claude-session",
            commands=["go test ./... > /dev/null &", f"cat {task_output}"],
        )
    )
    backend = ClaudeLifecycleBackend(executor=agent.executor)

    deployer = backend.run_deployer(repository=repository, application="example", correction_feedback=None)

    assert deployer.session_id == "claude-session"


def test_codex_cli_failure_logs_combined_output_and_returns_it_as_correction_feedback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    judge_calls = 0

    def respond(request: CommandRequest) -> FakeRun:
        nonlocal judge_calls
        schema = turn_schema(request)
        if "architecture_summary_markdown" in schema["properties"]:
            output = {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
            }
        else:
            judge_calls += 1
            if judge_calls == 1:
                return failure(
                    stdout='{"type":"error","message":"Go output was incomplete"}\n',
                    stderr="unexpected end of file before func TestDetector\n",
                )
            deployer = DeployerAssessment(
                session_id="deployer-session",
                source_commit=str(raw["source_commit"]),
                topology_fingerprint=str(raw["topology_fingerprint"]),
                resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
                architecture_summary_markdown="# Architecture\n\nExample Deployment and Service.",
            )
            artifact = _artifact(
                repository,
                session_id="placeholder",
                round_index=judge_calls - 1,
                deployer=deployer,
            )
            if "detector_source" in schema["properties"]:
                output = artifact.model_dump(mode="json", exclude={"session_id"})
            else:
                detector = Path(request.cwd or "") / ".sdo/diagnostics/detectors/health/objective"
                (detector / "detector.go").write_text(artifact.detector_source, encoding="utf-8")
                (detector / "detector_test.go").write_text(artifact.detector_test_source, encoding="utf-8")
                output = artifact.model_dump(
                    mode="json",
                    exclude={"session_id", "detector_source", "detector_test_source", "traffic_files"},
                )
        return reply("codex", output, session_id=f"fresh-session-{len(agent.requests)}")

    agent = ScriptedAgent(respond)

    with caplog.at_level(logging.WARNING, logger="sdo.agent_runtime.lifecycle.operational_memory"):
        run_initial_lifecycle(
            repository,
            application="example",
            health_objective="Deployment example and Service example must remain available.",
            backend=CodexLifecycleBackend(executor=agent.executor),
            validator=PassingValidator(),
            judge_rounds=3,
        )

    prompts = agent.prompts
    combined_failure = (
        'unexpected end of file before func TestDetector\n{"type":"error","message":"Go output was incomplete"}'
    )
    assert combined_failure in caplog.text
    assert combined_failure in prompts[2]


def test_health_judge_resource_mismatch_feedback_identifies_exact_tuple(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    deployer = DeployerAssessment(
        session_id="deployer",
        source_commit=str(raw["source_commit"]),
        topology_fingerprint=str(raw["topology_fingerprint"]),
        resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
        architecture_summary_markdown="# Architecture\n\nExample Deployment and Service.",
    )
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    mismatched = artifact.covered_resources[0].model_copy(update={"namespace": "runtime-namespace"})
    artifact = artifact.model_copy(update={"covered_resources": [mismatched]})

    errors = _validate_health_judge_artifact(
        artifact,
        deployer=deployer,
        health_objective="Deployment example and Service example must remain available.",
        expected_round=1,
    )

    assert any("namespace='runtime-namespace'" in error for error in errors)
    assert any("copy complete objects exactly" in error for error in errors)


def test_global_health_objective_requires_every_source_backed_deployment_and_service(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    deployer = DeployerAssessment(
        session_id="deployer",
        source_commit=str(raw["source_commit"]),
        topology_fingerprint=str(raw["topology_fingerprint"]),
        resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
        architecture_summary_markdown="# Architecture\n\nExample Deployment and Service.",
    )
    deployer = deployer.model_copy(
        update={
            "resources": [
                *deployer.resources,
                TopologyResourceDTO(
                    kind="ConfigMap",
                    name="example-config",
                    namespace="demo",
                    source="deploy.yaml",
                    dependencies=[],
                ),
                TopologyResourceDTO(
                    kind="NetworkPolicy",
                    name="example-policy",
                    namespace="demo",
                    source="deploy.yaml",
                    dependencies=[],
                ),
                TopologyResourceDTO(
                    kind="PersistentVolumeClaim",
                    name="example-data",
                    namespace="demo",
                    source="deploy.yaml",
                    dependencies=[],
                ),
            ]
        }
    )
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    artifact = artifact.model_copy(
        update={
            "covered_resources": [
                resource
                for resource in artifact.covered_resources
                if resource.kind not in {"Deployment", "ConfigMap", "NetworkPolicy"}
            ]
        }
    )

    errors = _validate_health_judge_artifact(
        artifact,
        deployer=deployer,
        health_objective=(
            "All source-backed Deployments remain available, all selected Services have ready endpoints, "
            "and representative requests succeed."
        ),
        expected_round=1,
    )

    assert any("omits required source-backed resources" in error for error in errors)
    assert any("Deployment/example" in error for error in errors)
    assert any("ConfigMap/example-config" in error for error in errors)
    assert any("NetworkPolicy/example-policy" in error for error in errors)
    assert any("must cover exactly" in error and "PersistentVolumeClaim/example-data" in error for error in errors)


def test_global_health_objective_does_not_mandate_a_fault_specific_check(tmp_path: Path) -> None:
    """The validator checks the judge's contract, not whether it wrote a check for a particular fault class.

    It once rejected every global-objective detector that did not derive missing ConfigMap
    references from pod templates: a check chosen because it matches a benchmark fault.
    """
    repository = _repository(tmp_path)
    manifest = repository / "deploy.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "      containers: [{name: example, image: example:v1}]",
            "      containers: [{name: example, image: example:v1}]\n"
            "      volumes: [{name: script, configMap: {name: runtime-script}}]",
        ),
        encoding="utf-8",
    )
    _git(repository, "add", "deploy.yaml")
    _git(repository, "commit", "-q", "-m", "reference generated runtime config")
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    deployer = DeployerAssessment(
        session_id="deployer",
        source_commit=str(raw["source_commit"]),
        topology_fingerprint=str(raw["topology_fingerprint"]),
        resources=[TopologyResourceDTO.model_validate(item) for item in raw["resources"]],
        architecture_summary_markdown="# Architecture\n\nExample Deployment and Service use runtime-script.",
    )
    objective = (
        "All source-backed Deployments remain available, all selected Services have ready endpoints, "
        "and representative requests succeed."
    )
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    artifact = artifact.model_copy(
        update={
            "objective_digest": hashlib.sha256(objective.encode()).hexdigest(),
            "detector_source": artifact.detector_source.replace(
                artifact.objective_digest, hashlib.sha256(objective.encode()).hexdigest()
            ),
        }
    )
    assert "ConfigMaps()" not in artifact.detector_source

    errors = _validate_health_judge_artifact(
        artifact,
        deployer=deployer,
        health_objective=objective,
        expected_round=1,
    )

    assert errors == []


def test_lifecycle_agents_never_run_in_a_directory_named_after_the_benchmark_stage(tmp_path: Path) -> None:
    """A stage directory such as ``r1-s1-missing-configmap`` names the injected fault; agents must not see it."""
    stage = tmp_path / "r1-s1-missing-configmap"
    stage.mkdir()
    repository = _repository(stage)

    class CwdRecordingBackend(RecordingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.repositories: list[Path] = []

        def run_deployer(
            self,
            *,
            repository: Path,
            application: str,
            correction_feedback: str | None,
        ) -> DeployerAssessment:
            self.repositories.append(repository)
            return super().run_deployer(
                repository=repository, application=application, correction_feedback=correction_feedback
            )

        def run_health_judge(self, **kwargs: object) -> HealthJudgeArtifact:
            self.repositories.append(cast("Path", kwargs["repository"]))
            return super().run_health_judge(**kwargs)  # pyright: ignore[reportArgumentType]

    backend = CwdRecordingBackend()

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend,
        validator=PassingValidator(),
        judge_rounds=3,
    )

    assert len(backend.repositories) == 4
    for seen in backend.repositories:
        assert "missing-configmap" not in str(seen)
        assert seen.name == "application"
    assert (repository / ".sdo/lifecycle-provenance.yaml").is_file()


@pytest.mark.live_agents
@pytest.mark.skipif(
    os.getenv("SDO_RUN_LIVE_AGENTS", "").strip() != "1",
    reason="set SDO_RUN_LIVE_AGENTS=1 to spend model tokens on the real three-round lifecycle",
)
@pytest.mark.parametrize(
    ("backend_type", "model_variable"),
    [
        pytest.param(CodexLifecycleBackend, "SDO_LIVE_CODEX_MODEL", id="codex"),
        pytest.param(ClaudeLifecycleBackend, "SDO_LIVE_CLAUDE_MODEL", id="claude"),
    ],
)
def test_real_three_round_health_judge_authors_compiling_detector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_type: type[CodexLifecycleBackend],
    model_variable: str,
) -> None:
    if shutil.which(backend_type.provider) is None:
        pytest.skip(f"{backend_type.provider} is not installed")
    repository = _repository(tmp_path)
    # This opt-in integration uses the explicit development-only validator.
    # Reuse the host module cache across all three candidates so the test
    # measures generated detector validity, not repeated dependency downloads.
    go_module_cache = subprocess.run(
        ["go", "env", "GOMODCACHE"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("GOMODCACHE", go_module_cache)

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        backend=backend_type(model=os.getenv(model_variable) or None, timeout_seconds=900),
        validator=LocalSandboxRunner(timeout_seconds=300),
        judge_rounds=3,
    )

    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    # Provenance records every attempt, so a corrected round appears more than once.
    attempts = provenance["health_judge_rounds"]
    assert {item["round"] for item in attempts} == {1, 2, 3}
    assert len({item["session_id"] for item in attempts}) == len(attempts)
    assert provenance["health_judge"]["round"] == 3
    assert LocalSandboxRunner(timeout_seconds=300).run(repository).returncode == 0


def _validated_seed(tmp_path: Path, objective: str) -> Path:
    repository = _repository(tmp_path)
    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=objective,
        backend=RecordingBackend(),
        validator=IdentifiedPassingValidator("validator-image@sha256:seed"),
        judge_rounds=3,
    )
    return repository


def _workspace_copy(seed: Path, destination: Path) -> Path:
    shutil.copytree(seed, destination)
    return destination


def test_validation_cache_shares_a_validator_verdict_across_discarded_workspace_copies(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    seed = _validated_seed(tmp_path, objective)
    cache = LifecycleValidationCache(tmp_path / "cache")

    first = IdentifiedPassingValidator("validator-image@sha256:rebuilt")
    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "stage-0"),
        application="example",
        health_objective=objective,
        validator=first,
        validation_cache=cache,
    )
    assert len(first.runs) == 1
    assert cache.source == "validator"

    # A later pipeline starts again from the seed, whose attestation names the old validator.
    second_workspace = _workspace_copy(seed, tmp_path / "stage-0-next-pipeline")
    second = IdentifiedPassingValidator("validator-image@sha256:rebuilt")
    assert reuse_initial_lifecycle_if_valid(
        second_workspace,
        application="example",
        health_objective=objective,
        validator=second,
        validation_cache=cache,
    )
    assert second.runs == []
    assert cache.source == "validation-cache"
    provenance = yaml.safe_load((second_workspace / ".sdo/lifecycle-provenance.yaml").read_text(encoding="utf-8"))
    assert provenance["validation"]["attested_by"] == "validation-cache"
    assert provenance["validation"]["validator_identity"] == "validator-image@sha256:rebuilt"


def test_validation_cache_is_keyed_by_validator_identity_and_diagnostics(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    seed = _validated_seed(tmp_path, objective)
    cache = LifecycleValidationCache(tmp_path / "cache")
    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "a"),
        application="example",
        health_objective=objective,
        validator=IdentifiedPassingValidator("validator-image@sha256:v2"),
        validation_cache=cache,
    )

    other_validator = IdentifiedPassingValidator("validator-image@sha256:v3")
    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "b"),
        application="example",
        health_objective=objective,
        validator=other_validator,
        validation_cache=cache,
    )
    assert len(other_validator.runs) == 1

    changed = _workspace_copy(seed, tmp_path / "c")
    detector = changed / ".sdo/diagnostics/detectors/health/objective/detector.go"
    detector.write_text(detector.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
    changed_diagnostics = IdentifiedPassingValidator("validator-image@sha256:v2")
    assert reuse_initial_lifecycle_if_valid(
        changed,
        application="example",
        health_objective=objective,
        validator=changed_diagnostics,
        validation_cache=cache,
    )
    assert len(changed_diagnostics.runs) == 1


def test_validation_cache_never_records_a_failed_or_unidentified_validation(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    seed = _validated_seed(tmp_path, objective)
    cache = LifecycleValidationCache(tmp_path / "cache")

    class FailingValidator(IdentifiedPassingValidator):
        def run(self, app_root: Path) -> SandboxResult:
            self.runs.append(app_root)
            return SandboxResult(returncode=1, stderr="go test failed")

    assert not reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "failed"),
        application="example",
        health_objective=objective,
        validator=FailingValidator("validator-image@sha256:v2"),
        validation_cache=cache,
    )
    unidentified = PassingValidator()
    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "unidentified"),
        application="example",
        health_objective=objective,
        validator=unidentified,
        validation_cache=cache,
    )
    assert cache.source == "validator"

    retried = IdentifiedPassingValidator("validator-image@sha256:v2")
    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "retried"),
        application="example",
        health_objective=objective,
        validator=retried,
        validation_cache=cache,
    )
    assert len(retried.runs) == 1


def test_validation_cache_reports_an_existing_workspace_attestation(tmp_path: Path) -> None:
    objective = "Deployment example and Service example must remain available."
    seed = _validated_seed(tmp_path, objective)
    cache = LifecycleValidationCache(tmp_path / "cache")

    assert reuse_initial_lifecycle_if_valid(
        _workspace_copy(seed, tmp_path / "attested"),
        application="example",
        health_objective=objective,
        validator=IdentifiedPassingValidator("validator-image@sha256:seed"),
        validation_cache=cache,
    )
    assert cache.source == "workspace-attestation"


def test_validation_cache_is_opt_in_through_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDO_LIFECYCLE_VALIDATION_CACHE_DIR", raising=False)
    assert LifecycleValidationCache.from_env() is None
    monkeypatch.setenv("SDO_LIFECYCLE_VALIDATION_CACHE_DIR", str(tmp_path / "cache"))
    cache = LifecycleValidationCache.from_env()
    assert cache is not None
    assert cache.directory == tmp_path / "cache"
    assert cache.source is None
    with pytest.raises(ValueError, match="absolute"):
        LifecycleValidationCache(Path("relative/cache"))
