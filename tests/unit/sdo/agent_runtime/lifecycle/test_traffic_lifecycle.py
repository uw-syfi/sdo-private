"""The lifecycle installs health-judge traffic mixes and the generic symptom detectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from controller.builder.manifest import load_manifest
from sdo.agent_runtime.lifecycle.agents import (
    ActiveTopologyResourceDTO,
    AuthoredTrafficMix,
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

EXAMPLE_MIX = """apiVersion: sdo.dev/v1alpha1
kind: TrafficMix
name: web
target:
  service: example
  port: 80
routes:
  - id: home
    method: GET
    path: /
"""


def _judged(tmp_path: Path, *, mixes: list[AuthoredTrafficMix]) -> tuple[Path, DeployerAssessment, HealthJudgeArtifact]:
    repository = _repository(tmp_path)
    deployer = RecordingBackend().run_deployer(repository=repository, application="example", correction_feedback=None)
    artifact = _artifact(repository, session_id="judge", round_index=1, deployer=deployer)
    return repository, deployer, artifact.model_copy(update={"traffic_mixes": mixes})


def test_judge_output_schema_requires_traffic_mixes_for_strict_structured_turns() -> None:
    schema = HealthJudgeDraft.model_json_schema()
    assert "traffic_mixes" in schema["required"]
    mix_schema = schema["$defs"]["AuthoredTrafficMix"]
    assert sorted(mix_schema["required"]) == sorted(mix_schema["properties"])


def test_lifecycle_installs_mix_traffic_detector_and_endpoint_detector(tmp_path: Path) -> None:
    mixes = [AuthoredTrafficMix(name="web", document_yaml=EXAMPLE_MIX)]
    repository, deployer, artifact = _judged(tmp_path, mixes=mixes)

    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    assert [mix.name for mix in MemoryRepository(repository).traffic_mixes()] == ["web"]
    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    registrations = {detector.id: detector for detector in manifest.detectors}
    assert sorted(registrations) == ["health-objective", "service-endpoints", "traffic-web"]
    for detector_id in ("service-endpoints", "traffic-web"):
        registration = registrations[detector_id]
        assert registration.detector_class == "health"
        assert registration.owner == "health_judge"
        assert registration.package.startswith("./detectors/health/")
    assert [(watch.api_version, watch.kind) for watch in registrations["traffic-web"].watches] == [
        ("sdo.dev/v1alpha1", "SyntheticTraffic")
    ]
    source = (repository / ".sdo/diagnostics/detectors/health/traffic-web/detector.go").read_text(encoding="utf-8")
    assert "traffic.NewDetector(" in source
    assert '"web")' in source
    MemoryValidator(run_diagnostics=False).validate(
        repository,
        actor=ArtifactOwner.HEALTH_JUDGE,
        changed_paths=[".sdo/diagnostics/traffic/web.yaml"],
        baseline_root=repository,
    )


def test_lifecycle_without_mixes_still_installs_the_endpoint_detector(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(tmp_path, mixes=[])

    ensure_operational_memory(
        repository,
        application="example",
        health_objective=OBJECTIVE,
        health_judge_artifact=artifact,
        architecture_summary_markdown=deployer.architecture_summary_markdown,
    )

    manifest = load_manifest(repository / ".sdo/diagnostics/manifest.yaml", app_root=repository)
    assert sorted(detector.id for detector in manifest.detectors) == ["health-objective", "service-endpoints"]
    assert MemoryRepository(repository).traffic_mixes() == []


def test_judge_mix_must_be_valid_and_target_a_source_backed_service(tmp_path: Path) -> None:
    repository, deployer, artifact = _judged(
        tmp_path,
        mixes=[
            AuthoredTrafficMix(name="web", document_yaml=EXAMPLE_MIX.replace("service: example", "service: ghost")),
            AuthoredTrafficMix(name="admin", document_yaml=EXAMPLE_MIX),
            AuthoredTrafficMix(name="bad", document_yaml=EXAMPLE_MIX.replace("name: web", "name: bad").replace(
                "path: /", "path: http://evil.example/")),
        ],
    )

    errors = _validate_health_judge_artifact(
        artifact, deployer=deployer, health_objective=OBJECTIVE, expected_round=1
    )

    joined = "\n".join(errors)
    assert "Service/ghost" in joined
    assert "'admin'" in joined
    assert "'bad'" in joined
    assert "path" in joined
    del repository


def test_workspace_judge_may_author_traffic_mixes_but_nothing_else_new(tmp_path: Path) -> None:
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
            authored = _artifact(repository, session_id=f"workspace-{round_index}", round_index=round_index,
                                 deployer=deployer)
            traffic = repository / ".sdo/diagnostics/traffic"
            traffic.mkdir(parents=True, exist_ok=True)
            (traffic / "web.yaml").write_text(EXAMPLE_MIX, encoding="utf-8")
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

    assert (repository / ".sdo/diagnostics/traffic/web.yaml").read_text(encoding="utf-8") == EXAMPLE_MIX
    provenance = yaml.safe_load((repository / ".sdo/lifecycle-provenance.yaml").read_text(encoding="utf-8"))
    assert [mix["name"] for mix in provenance["health_judge"]["traffic_mixes"]] == ["web"]
    assert (repository / ".sdo/diagnostics/detectors/health/traffic-web/detector.go").is_file()


def test_judge_prompts_ask_for_a_source_grounded_traffic_mix(tmp_path: Path) -> None:
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
    for expected in (".sdo/diagnostics/traffic/", "syntheticMarker", "dataPolicy", "read and write", ".sdo/arch.md"):
        assert expected in prompt
