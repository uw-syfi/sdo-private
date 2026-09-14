from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    CodexLifecycleBackend,
    DeployerAssessment,
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
from sdo.operational_memory.sandbox import LocalSandboxRunner, SandboxResult


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
    calls: list[list[str]] = []
    prompts: list[str] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        prompts.append(str(kwargs["input"]))
        output_path = Path(command[command.index("--output-last-message") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if "architecture_summary_markdown" in schema["properties"]:
            output = {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "resources": raw["resources"],
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
        output_path.write_text(json.dumps(output), encoding="utf-8")
        session = f"fresh-session-{len(calls)}"
        return subprocess.CompletedProcess(command, 0, f'{{"type":"thread.started","thread_id":"{session}"}}\n', "")

    backend = CodexLifecycleBackend(command_runner=runner)
    deployer = backend.run_deployer(
        repository=repository,
        application="example",
        correction_feedback=None,
    )
    judge = backend.run_health_judge(
        repository=repository,
        application="example",
        health_objective="Deployment example and Service example must remain available.",
        deployer=deployer,
        round_index=1,
        previous=None,
        correction_feedback=None,
    )

    assert deployer.session_id == "fresh-session-1"
    assert judge.session_id == "fresh-session-2"
    assert len(calls) == 2
    assert all("resume" not in command for command in calls)
    assert all(command[command.index("--sandbox") + 1] == "read-only" for command in calls)
    assert all("--output-schema" in command and "--json" in command for command in calls)
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


def test_codex_backend_uses_managed_process_group_execution_by_default() -> None:
    backend = CodexLifecycleBackend()

    assert backend.command_runner is None


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

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "source_commit": raw["source_commit"],
                    "topology_fingerprint": raw["topology_fingerprint"],
                    "resources": raw["resources"],
                    "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
                }
            ),
            encoding="utf-8",
        )
        stdout = "\n".join(
            [
                '{"type":"thread.started","thread_id":"fresh-session"}',
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": escaped_command,
                        },
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    backend = CodexLifecycleBackend(command_runner=runner)

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


def test_repository_audit_still_rejects_external_path_after_git_object_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    assert _command_escapes_repository(
        "git show ed44ea9:/.sdo/goal.md; cat /etc/passwd",
        repository,
    )


def test_codex_cli_failure_logs_combined_output_and_returns_it_as_correction_feedback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository(tmp_path)
    raw = _deployer_assessment({"repository": str(repository), "application": "example"})
    prompts: list[str] = []
    judge_calls = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal judge_calls
        prompts.append(str(kwargs["input"]))
        output_path = Path(command[command.index("--output-last-message") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if "architecture_summary_markdown" in schema["properties"]:
            output = {
                "source_commit": raw["source_commit"],
                "topology_fingerprint": raw["topology_fingerprint"],
                "resources": raw["resources"],
                "architecture_summary_markdown": "# Architecture\n\nExample Deployment and Service.",
            }
        else:
            judge_calls += 1
            if judge_calls == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    '{"type":"error","message":"Go output was incomplete"}\n',
                    "unexpected end of file before func TestDetector\n",
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
                candidate = Path(command[command.index("--cd") + 1])
                detector = candidate / ".sdo/diagnostics/detectors/health/objective"
                (detector / "detector.go").write_text(artifact.detector_source, encoding="utf-8")
                (detector / "detector_test.go").write_text(artifact.detector_test_source, encoding="utf-8")
                output = artifact.model_dump(
                    mode="json",
                    exclude={"session_id", "detector_source", "detector_test_source"},
                )
        output_path.write_text(json.dumps(output), encoding="utf-8")
        session = f"fresh-session-{len(prompts)}"
        return subprocess.CompletedProcess(command, 0, f'{{"type":"thread.started","thread_id":"{session}"}}\n', "")

    with caplog.at_level(logging.WARNING, logger="sdo.agent_runtime.lifecycle.operational_memory"):
        run_initial_lifecycle(
            repository,
            application="example",
            health_objective="Deployment example and Service example must remain available.",
            backend=CodexLifecycleBackend(command_runner=runner),
            validator=PassingValidator(),
            judge_rounds=3,
        )

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


def test_global_health_objective_requires_dynamic_missing_configmap_dependency_detection(tmp_path: Path) -> None:
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
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)

    errors = _validate_health_judge_artifact(
        artifact,
        deployer=deployer,
        health_objective=(
            "All source-backed Deployments remain available, all selected Services have ready endpoints, "
            "and representative requests succeed."
        ),
        expected_round=1,
    )

    assert any("derive missing ConfigMap dependencies from Deployment pod specs" in error for error in errors)
    assert any("ConfigMap/runtime-script" in error for error in errors)

    helper_artifact = artifact.model_copy(
        update={
            "detector_source": artifact.detector_source
            + "\n// uses sdk.ConfigMapReferencesForDeployment(deployment) with snapshot.ConfigMaps()\n"
        }
    )
    helper_errors = _validate_health_judge_artifact(
        helper_artifact,
        deployer=deployer,
        health_objective=(
            "All source-backed Deployments remain available, all selected Services have ready endpoints, "
            "and representative requests succeed."
        ),
        expected_round=1,
    )

    assert not any(
        "derive missing ConfigMap dependencies from Deployment pod specs" in error for error in helper_errors
    )


@pytest.mark.live_codex
@pytest.mark.skipif(
    os.getenv("SDO_RUN_LIVE_CODEX", "").strip() != "1",
    reason="set SDO_RUN_LIVE_CODEX=1 to spend model tokens on the real three-round lifecycle",
)
def test_real_codex_three_round_health_judge_authors_compiling_detector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        backend=CodexLifecycleBackend(timeout_seconds=900),
        validator=LocalSandboxRunner(timeout_seconds=300),
        judge_rounds=3,
    )

    provenance = __import__("yaml").safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text())
    assert len(provenance["health_judge_rounds"]) == 3
    assert len({item["session_id"] for item in provenance["health_judge_rounds"]}) == 3
    assert LocalSandboxRunner(timeout_seconds=300).run(repository).returncode == 0
