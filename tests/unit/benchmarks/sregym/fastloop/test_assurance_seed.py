from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.fastloop.assurance.seed import (
    DEFAULT_SEED,
    SEEDS_DIR,
    SeedError,
    build_seed_repository,
)

if TYPE_CHECKING:
    from pathlib import Path


def _log(repository: Path) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(repository), "log", "--format=%an|%s"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def _fake_sregym(tmp_path: Path) -> Path:
    source = tmp_path / "sregym" / "SREGym-applications" / "hotelReservation"
    (source / "cmd").mkdir(parents=True)
    (source / "cmd" / "main.go").write_text("package main\n", encoding="utf-8")
    (source / ".gitmodules").write_text("[submodule]\n", encoding="utf-8")
    return tmp_path / "sregym"


def test_the_seed_is_the_source_snapshot_then_the_lifecycle_memory(tmp_path: Path) -> None:
    target = tmp_path / "seed"

    build_seed_repository(target, sregym_dir=_fake_sregym(tmp_path))

    assert _log(target) == [
        "SDO Lifecycle|sdo: capture goal, architecture, and independent health judge",
        "SREGym|Initial application workspace snapshot",
    ]
    assert (target / "cmd" / "main.go").is_file()
    assert not (target / ".gitmodules").exists()
    manifest = (target / ".sdo" / "diagnostics" / "manifest.yaml").read_text(encoding="utf-8")
    for detector in ("health-objective", "service-endpoints", "traffic-health"):
        assert f"id: {detector}" in manifest
    assert (target / ".sdo" / "diagnostics" / "traffic" / "workloads" / "verify.yaml").is_file()
    assert (target / ".sdo" / "outcomes.jsonl").read_text(encoding="utf-8") == ""


def test_the_checked_in_seed_has_the_health_judges_generators() -> None:
    generators = SEEDS_DIR / DEFAULT_SEED / "operational_memory" / "diagnostics" / "traffic" / "generators"
    assert "hotel-search" in (generators / "hotel_reservation.go").read_text(encoding="utf-8")


def test_the_seed_needs_the_sregym_application_source(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="submodule update"):
        build_seed_repository(tmp_path / "seed", sregym_dir=tmp_path / "missing")


def test_the_seed_never_overwrites_a_directory(tmp_path: Path) -> None:
    (tmp_path / "seed").mkdir()
    with pytest.raises(SeedError, match="already exists"):
        build_seed_repository(tmp_path / "seed", sregym_dir=_fake_sregym(tmp_path))
