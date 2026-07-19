# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from sdo.agent_runtime.lifecycle.agents import (
    CodexLifecycleBackend,
    DeployerAssessment,
    HealthJudgeArtifact,
    TopologyResourceDTO,
)
from sdo.agent_runtime.lifecycle.operational_memory import (
    _HEALTH_DETECTOR_TEST_SOURCE,
    LifecycleError,
    _canonicalize_health_registration,
    _deployer_assessment,
    _judge_assessment,
    _render_health_detector,
    _validate_health_judge_artifact,
    reuse_initial_lifecycle_if_valid,
    run_initial_lifecycle,
)
from sdo.operational_memory.sandbox import LocalSandboxRunner, SandboxResult


def _topology_resources(value: object) -> list[TopologyResourceDTO]:
    return [TopologyResourceDTO.model_validate(resource) for resource in cast("list[object]", value)]


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
        covered_resources=_topology_resources(deployer.model_dump(mode="json")["resources"]),
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
            resources=_topology_resources(raw["resources"]),
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
    ) -> HealthJudgeArtifact:
        del application, health_objective
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
    \"sdo.dev/controller/sdk\"
)

func New() sdk.Detector { return Detector{} }
type Detector struct{}
func (Detector) Spec() sdk.DetectorSpec {
    _ = appsv1.SchemeGroupVersion
    return sdk.DetectorSpec{}
}
func (Detector) Detect(ctx context.Context, snap sdk.DetectionContext) ([]sdk.Finding, error) {
    _ = corev1.ConditionTrue
    return nil, nil
}
"""

    canonical = _canonicalize_health_registration(source)

    assert 'appsv1 "k8s.io/api/apps/v1"' not in canonical
    assert 'corev1 "k8s.io/api/core/v1"' in canonical


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
                resources=_topology_resources(raw["resources"]),
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
    assert "Every Go func declaration must be package-level" in prompts[1]
    assert "Mentally parse both complete files before returning them" in prompts[1].replace("\n", " ")


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
                resources=_topology_resources(raw["resources"]),
                architecture_summary_markdown="# Architecture\n\nExample Deployment and Service.",
            )
            artifact = _artifact(
                repository,
                session_id="placeholder",
                round_index=judge_calls - 1,
                deployer=deployer,
            )
            output = artifact.model_dump(mode="json", exclude={"session_id"})
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
        resources=_topology_resources(raw["resources"]),
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
        resources=_topology_resources(raw["resources"]),
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
        resources=_topology_resources(raw["resources"]),
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
