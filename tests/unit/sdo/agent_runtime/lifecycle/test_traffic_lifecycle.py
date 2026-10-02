"""The lifecycle installs health-judge synthetic traffic and the generic symptom detectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from controller.builder.manifest import load_manifest
from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    AuthoredTrafficFile,
    CodexLifecycleBackend,
    DeployerAssessment,
    HealthJudgeArtifact,
    HealthJudgeDraft,
    HealthJudgeWorkspaceArtifact,
)
from sdo.agent_runtime.lifecycle.operational_memory import (
    _validate_health_judge_artifact,
    ensure_operational_memory,
    run_initial_lifecycle,
)
from sdo.operational_memory.models import ArtifactOwner
from sdo.operational_memory.repository import MemoryRepository
from sdo.operational_memory.validation import MemoryValidator
from tests.structured_turns import ScriptedAgent, reply
from tests.unit.sdo.agent_runtime.lifecycle.test_agents import (
    PassingValidator,
    RecordingBackend,
    _artifact,
    _repository,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import CommandRequest
    from agentshim.testing import FakeRun

OBJECTIVE = "Deployment example and Service example must remain available."

GENERATORS = """package generators

import "sdo.dev/controller/sdk/traffic"

func Scenarios() traffic.Catalog {
\treturn traffic.Catalog{{
\t\tID: "home", Target: traffic.Target{Service: "example", Port: 80}, SideEffect: traffic.SideEffectRead,
\t\tSteps: []traffic.Step{{Name: "home", Endpoint: traffic.GET("/", nil)}},
\t}}
}
"""


def _workload(name: str, purpose: str = "health-probe") -> str:
    document: dict[str, object] = {
        "apiVersion": "sdo.dev/v1alpha1",
        "kind": "TrafficWorkload",
        "name": name,
        "purpose": purpose,
        "scenarios": [{"id": "home"}],
    }
    if purpose != "health-probe":
        document["duration"] = "3s"
    return yaml.safe_dump(document)


def _files(**overrides: str) -> list[AuthoredTrafficFile]:
    files = {
        "generators/generators.go": GENERATORS,
        "workloads/health.yaml": _workload("health"),
        "workloads/verify.yaml": _workload("verify", "verify-burst"),
        **overrides,
    }
    return [AuthoredTrafficFile(path=path, content=content) for path, content in files.items()]


def _judged(
    tmp_path: Path, *, files: list[AuthoredTrafficFile]
) -> tuple[Path, DeployerAssessment, HealthJudgeArtifact]:
    repository = _repository(tmp_path)
    deployer = RecordingBackend().run_deployer(repository=repository, application="example", correction_feedback=None)
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    return repository, deployer, artifact.model_copy(update={"traffic_files": files})


def test_judge_output_schema_requires_traffic_files_for_strict_structured_turns() -> None:
    schema = HealthJudgeDraft.model_json_schema()
    assert "traffic_files" in schema["required"]
    file_schema = schema["$defs"]["AuthoredTrafficFile"]
    assert sorted(file_schema["required"]) == sorted(file_schema["properties"])


def test_lifecycle_installs_traffic_and_one_detector_per_health_probe_workload(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(tmp_path, files=_files())

    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    traffic = repository / ".sdo" / "diagnostics" / "traffic"
    assert (traffic / "generators" / "generators.go").read_text(encoding="utf-8") == GENERATORS
    assert [workload.name for workload in MemoryRepository(repository).traffic_workloads()] == [
        "health",
        "topology-links",
        "verify",
    ]
    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    registrations = {detector.id: detector for detector in manifest.detectors}
    assert sorted(registrations) == [
        "health-objective",
        "service-endpoints",
        "traffic-health",
        "traffic-topology-links",
    ]
    for detector_id in ("service-endpoints", "traffic-health"):
        registration = registrations[detector_id]
        assert registration.detector_class == "health"
        assert registration.owner == "health_judge"
        assert registration.package.startswith("./detectors/health/")
    assert [(watch.api_version, watch.kind) for watch in registrations["traffic-health"].watches] == [
        ("sdo.dev/v1alpha1", "SyntheticTraffic")
    ]
    source = (repository / ".sdo/diagnostics/detectors/health/traffic-health/detector.go").read_text(encoding="utf-8")
    assert "traffic.NewDetector(" in source
    assert '"health")' in source
    MemoryValidator(run_diagnostics=False).validate(
        repository,
        actor=ArtifactOwner.HEALTH_JUDGE,
        changed_paths=[".sdo/diagnostics/traffic/workloads/health.yaml"],
        baseline_root=repository,
    )


def test_lifecycle_without_traffic_still_installs_the_endpoint_detector(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(tmp_path, files=[])

    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    assert sorted(detector.id for detector in manifest.detectors) == [
        "health-objective",
        "service-endpoints",
        "traffic-topology-links",
    ]
    assert [workload.name for workload in MemoryRepository(repository).traffic_workloads()] == ["topology-links"]


def test_judge_traffic_must_be_valid_and_target_source_backed_services(tmp_path: Path) -> None:
    _, deployer, artifact = _judged(
        tmp_path,
        files=_files(
            **{
                "generators/generators.go": GENERATORS.replace('Service: "example"', 'Service: "ghost"'),
                "workloads/health.yaml": _workload("admin"),
                "workloads/incident-x.yaml": _workload("incident-x", "journey"),
                "workloads/verify.yaml": _workload("verify").replace("scenarios", "sceanrios"),
            }
        ),
    )

    errors = _validate_health_judge_artifact(artifact, deployer=deployer, health_objective=OBJECTIVE, expected_round=1)

    joined = "\n".join(errors)
    assert "Service/ghost" in joined
    assert "declares name 'admin'" in joined
    assert "incident-" in joined
    assert "workloads/verify.yaml' is invalid" in joined


def test_judge_generators_need_a_health_probe_workload(tmp_path: Path) -> None:
    _, deployer, artifact = _judged(
        tmp_path, files=[AuthoredTrafficFile(path="generators/generators.go", content=GENERATORS)]
    )

    errors = _validate_health_judge_artifact(artifact, deployer=deployer, health_objective=OBJECTIVE, expected_round=1)

    assert any("health-probe workload" in error for error in errors)


def test_workspace_judge_may_author_traffic_but_nothing_else_new(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

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
            authored = _artifact(
                repository, session_id=f"workspace-{round_index}", round_index=round_index, deployer=deployer
            )
            traffic = repository / ".sdo/diagnostics/traffic"
            for authored_file in _files():
                path = traffic / authored_file.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(authored_file.content, encoding="utf-8")
            return HealthJudgeWorkspaceArtifact(
                session_id=authored.session_id,
                round=round_index,
                objective_digest=authored.objective_digest,
                source_commit=deployer.source_commit,
                covered_resources=authored.covered_resources,
                failure_patterns=authored.failure_patterns,
            )

    run_initial_lifecycle(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        backend=WorkspaceBackend(),
        validator=PassingValidator(),
        judge_rounds=1,
    )

    traffic = repository / ".sdo/diagnostics/traffic"
    assert (traffic / "generators" / "generators.go").read_text(encoding="utf-8") == GENERATORS
    provenance = yaml.safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text(encoding="utf-8"))
    assert [item["path"] for item in provenance["health_judge"]["traffic_files"]] == [
        "generators/generators.go",
        "workloads/health.yaml",
        "workloads/verify.yaml",
    ]
    assert (repository / ".sdo/diagnostics/detectors/health/traffic-health/detector.go").is_file()


def test_judge_prompts_ask_for_source_grounded_generators_and_workloads(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    deployer = RecordingBackend().run_deployer(repository=repository, application="example", correction_feedback=None)
    artifact = _artifact(repository, session_id="placeholder", round_index=1, deployer=deployer)

    def respond(request: CommandRequest) -> FakeRun:
        del request
        return reply("codex", artifact.model_dump(mode="json", exclude={"session_id"}), session_id="judge-session")

    agent = ScriptedAgent(respond)
    CodexLifecycleBackend(executor=agent.executor).run_health_judge(
        repository=repository,
        application="example",
        health_objective=OBJECTIVE,
        deployer=deployer,
        round_index=1,
        previous=None,
        correction_feedback=None,
    )

    prompt = agent.prompts[0]
    for expected in (
        ".sdo/diagnostics/traffic/",
        "generators/",
        "workloads/<name>.yaml",
        "health-probe",
        "verify-burst",
        "DependsOn",
        "Marker",
        "read and write",
        ".sdo/arch.md",
        "traffic_files",
    ):
        assert expected in prompt
