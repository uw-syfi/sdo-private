"""Traffic generators and workloads are owned, validated operational memory."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sdo.operational_memory.models import ArtifactOwner, TrafficWorkload
from sdo.operational_memory.repository import MemoryRepository, MemoryRepositoryError
from sdo.operational_memory.validation import MemoryValidationError, MemoryValidator
from tests.unit.sdo.operational_memory.test_memory import _write_memory

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "hotel_reservation" / "traffic"
HEALTH_WORKLOAD = ".sdo/diagnostics/traffic/workloads/health.yaml"
GENERATORS = ".sdo/diagnostics/traffic/generators/generators.go"
INCIDENT_WORKLOAD = ".sdo/diagnostics/traffic/workloads/incident-reserve.yaml"
INCIDENT_GENERATORS = ".sdo/diagnostics/traffic/generators/incident/reserve.go"


def _document(name: str = "health") -> dict[str, object]:
    return yaml.safe_load((FIXTURE / "workloads" / f"{name}.yaml").read_text(encoding="utf-8"))


def _with_traffic(root: Path) -> None:
    shutil.copytree(FIXTURE, root / ".sdo" / "diagnostics" / "traffic", dirs_exist_ok=True)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_hotel_fixture_workloads_are_valid_with_documented_defaults() -> None:
    health = TrafficWorkload.model_validate(_document("health"))
    verify = TrafficWorkload.model_validate(_document("verify"))

    assert health.purpose == "health-probe"
    assert health.duration is None
    assert [scenario.id for scenario in health.scenarios] == ["search-hotels", "recommend", "login"]
    assert health.scenario_slo("login").window == 5
    assert verify.purpose == "verify-burst"
    assert verify.duration == "3s"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda workload: workload.update(extra=True), "extra"),
        (lambda workload: workload.update(kind="TrafficMix"), "TrafficWorkload"),
        (lambda workload: workload.update(ratePerSecond=500), "ratePerSecond"),
        (lambda workload: workload.update(duration="5s"), "continuously"),
        (lambda workload: workload.update(purpose="verify-burst"), "duration"),
        (lambda workload: workload.update(timeout="20s"), "timeout"),
        (lambda workload: workload["scenarios"].append({"id": "login"}), "twice"),
        (lambda workload: workload.update(slo={"maxErrorRate": 1.5}), "maxErrorRate"),
        (lambda workload: workload.update(slo={"window": 2, "minSamples": 3}), "minSamples"),
    ],
)
def test_workload_rejects_unsafe_or_ambiguous_profiles(mutate, message: str) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(ValidationError, match=message):
        TrafficWorkload.model_validate(document)


def test_repository_loads_workloads_and_requires_matching_file_names(tmp_path: Path) -> None:
    _write_memory(tmp_path)
    _with_traffic(tmp_path)

    assert [workload.name for workload in MemoryRepository(tmp_path).traffic_workloads()] == [
        "health",
        "journey",
        "verify",
    ]

    _write(tmp_path, ".sdo/diagnostics/traffic/workloads/other.yaml", yaml.safe_dump(_document()))
    with pytest.raises(MemoryRepositoryError, match="file name"):
        MemoryRepository(tmp_path).traffic_workloads()


def test_traffic_directory_holds_only_generators_and_workloads(tmp_path: Path) -> None:
    _write_memory(tmp_path)
    assert MemoryRepository(tmp_path).traffic_workloads() == []
    _write(tmp_path, ".sdo/diagnostics/traffic/frontend.yaml", "kind: TrafficMix\n")

    with pytest.raises(MemoryRepositoryError, match="only generators/ and workloads/"):
        MemoryRepository(tmp_path).traffic_workloads()


def _validate(tmp_path: Path, actor: ArtifactOwner, changes: dict[str, str]) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    _with_traffic(baseline)
    shutil.copytree(baseline, candidate)
    for relative, text in changes.items():
        _write(candidate, relative, text)
    MemoryValidator(run_diagnostics=False).validate(
        candidate, actor=actor, changed_paths=list(changes), baseline_root=baseline
    )


def _relaxed_health() -> str:
    document = _document()
    document["slo"] = {"maxErrorRate": 1.0}
    return yaml.safe_dump(document)


def test_health_judge_owns_generators_and_health_workloads(tmp_path: Path) -> None:
    _validate(
        tmp_path,
        ArtifactOwner.HEALTH_JUDGE,
        {HEALTH_WORKLOAD: _relaxed_health(), GENERATORS: "package generators\n"},
    )


@pytest.mark.parametrize("actor", [ArtifactOwner.RESPONDER, ArtifactOwner.DEPLOYER, ArtifactOwner.UPKEEP])
@pytest.mark.parametrize("path", [HEALTH_WORKLOAD, GENERATORS])
def test_other_actors_may_not_weaken_the_health_acceptance_test(
    tmp_path: Path, actor: ArtifactOwner, path: str
) -> None:
    text = _relaxed_health() if path.endswith(".yaml") else "package generators\n"
    with pytest.raises(MemoryValidationError, match="does not own"):
        _validate(tmp_path, actor, {path: text})


def _incident_workload() -> str:
    document = _document("journey")
    document["name"] = "incident-reserve"
    return yaml.safe_dump(document)


def test_responders_may_add_incident_scoped_generators_and_workloads(tmp_path: Path) -> None:
    _validate(
        tmp_path,
        ArtifactOwner.RESPONDER,
        {INCIDENT_WORKLOAD: _incident_workload(), INCIDENT_GENERATORS: "package incident\n"},
    )


def test_the_health_judge_does_not_own_incident_traffic(tmp_path: Path) -> None:
    with pytest.raises(MemoryValidationError, match="does not own"):
        _validate(tmp_path, ArtifactOwner.HEALTH_JUDGE, {INCIDENT_WORKLOAD: _incident_workload()})


def test_an_invalid_workload_fails_every_memory_validation(tmp_path: Path) -> None:
    document = _document()
    document["duration"] = "5s"
    with pytest.raises(MemoryValidationError, match="continuously"):
        _validate(tmp_path, ArtifactOwner.HEALTH_JUDGE, {HEALTH_WORKLOAD: yaml.safe_dump(document)})
