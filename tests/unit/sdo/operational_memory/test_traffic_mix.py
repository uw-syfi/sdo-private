"""Traffic mixes are health-judge-owned, validated operational memory."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sdo.operational_memory.models import ArtifactOwner, TrafficMix
from sdo.operational_memory.repository import MemoryRepository, MemoryRepositoryError
from sdo.operational_memory.validation import MemoryValidationError, MemoryValidator
from tests.unit.sdo.operational_memory.test_memory import _write_memory

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "hotel_reservation" / "traffic" / "frontend.yaml"
TRAFFIC_PATH = ".sdo/diagnostics/traffic/frontend.yaml"


def _fixture() -> dict[str, object]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _with_mix(root: Path, document: dict[str, object] | None = None, *, name: str = "frontend") -> Path:
    traffic = root / ".sdo" / "diagnostics" / "traffic"
    traffic.mkdir(parents=True, exist_ok=True)
    path = traffic / f"{name}.yaml"
    path.write_text(yaml.safe_dump(document or _fixture(), sort_keys=False), encoding="utf-8")
    return path


def test_hotel_fixture_is_a_valid_mix_with_documented_defaults() -> None:
    mix = TrafficMix.model_validate(_fixture())

    assert mix.name == "frontend"
    assert mix.target.scheme == "http"
    assert [route.id for route in mix.routes] == ["search", "recommend", "login", "reserve"]
    assert [route.id for route in mix.routes if route.mutates] == ["reserve"]
    assert all(route.weight == 1 for route in mix.routes)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda mix: mix.update(extra=True), "extra"),
        (lambda mix: mix.update(kind="Other"), "TrafficMix"),
        (lambda mix: mix.update(ratePerSecond=500), "ratePerSecond"),
        (lambda mix: mix["routes"][0].update(path="http://evil.example/"), "path"),
        (lambda mix: mix["routes"][3].pop("dataPolicy"), "dataPolicy"),
        (lambda mix: mix["routes"][3].pop("syntheticMarker"), "syntheticMarker"),
        (lambda mix: mix["routes"][3]["query"].update(customerName="alice"), "syntheticMarker"),
        (lambda mix: mix["routes"][3].update(dataPolicy="self-cleaning"), "cleanup"),
        (lambda mix: mix.update(routes=[mix["routes"][3]]), "read route"),
        (lambda mix: mix["routes"].append(dict(mix["routes"][0])), "duplicate"),
        (lambda mix: mix.update(slo={"maxErrorRate": 1.5}), "maxErrorRate"),
    ],
)
def test_mix_rejects_unsafe_or_ambiguous_documents(mutate, message: str) -> None:
    document = _fixture()
    mutate(document)

    with pytest.raises(ValidationError, match=message):
        TrafficMix.model_validate(document)


def test_repository_loads_mixes_and_requires_matching_file_names(tmp_path: Path) -> None:
    _write_memory(tmp_path)
    _with_mix(tmp_path)

    assert [mix.name for mix in MemoryRepository(tmp_path).traffic_mixes()] == ["frontend"]

    _with_mix(tmp_path, name="storefront")
    with pytest.raises(MemoryRepositoryError, match="file name"):
        MemoryRepository(tmp_path).traffic_mixes()


def test_repository_without_traffic_mixes_is_valid(tmp_path: Path) -> None:
    _write_memory(tmp_path)

    assert MemoryRepository(tmp_path).traffic_mixes() == []


def test_health_judge_owns_traffic_mixes(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    shutil.copytree(baseline, candidate)
    _with_mix(candidate)

    MemoryValidator(run_diagnostics=False).validate(
        candidate, actor=ArtifactOwner.HEALTH_JUDGE, changed_paths=[TRAFFIC_PATH], baseline_root=baseline
    )


@pytest.mark.parametrize("actor", [ArtifactOwner.RESPONDER, ArtifactOwner.DEPLOYER, ArtifactOwner.UPKEEP])
def test_other_actors_may_not_edit_traffic_mixes(tmp_path: Path, actor: ArtifactOwner) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    _with_mix(baseline)
    shutil.copytree(baseline, candidate)
    document = _fixture()
    document["slo"] = {"maxErrorRate": 1.0}
    _with_mix(candidate, document)

    with pytest.raises(MemoryValidationError, match="does not own"):
        MemoryValidator(run_diagnostics=False).validate(
            candidate, actor=actor, changed_paths=[TRAFFIC_PATH], baseline_root=baseline
        )


def test_an_invalid_mix_fails_every_memory_validation(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    shutil.copytree(baseline, candidate)
    document = _fixture()
    document["routes"] = [route for route in document["routes"] if route["id"] == "reserve"]
    _with_mix(candidate, document)

    with pytest.raises(MemoryValidationError, match="read route"):
        MemoryValidator(run_diagnostics=False).validate(
            candidate, actor=ArtifactOwner.HEALTH_JUDGE, changed_paths=[TRAFFIC_PATH], baseline_root=baseline
        )
