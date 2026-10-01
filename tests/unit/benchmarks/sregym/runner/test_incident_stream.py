"""The deterministic incident stream: same seed, same incidents, learnable structure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

from benchmarks.sregym.runner.incident_stream import (
    HOTEL_CATALOG,
    NOVEL_COUNT,
    STREAM_LENGTH,
    STREAM_OPENING,
    STREAM_SEED,
    FaultFamily,
    StreamIncident,
    generate_stream,
    render_baseline_toml,
    render_manifest,
    render_sdo_pipeline_toml,
)

EXPERIMENTS = Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "experiments"
PILOT = 8


def _stream() -> list[StreamIncident]:
    return generate_stream(STREAM_SEED, STREAM_LENGTH, opening=STREAM_OPENING)


def test_the_same_seed_gives_the_same_stream_and_another_seed_a_different_one() -> None:
    assert _stream() == _stream()
    assert [i.problem_id for i in generate_stream(STREAM_SEED + 1, STREAM_LENGTH, opening=STREAM_OPENING)] != [
        i.problem_id for i in _stream()
    ]


def test_the_stream_starts_with_the_pinned_opening() -> None:
    assert tuple(i.problem_id for i in _stream()[: len(STREAM_OPENING)]) == STREAM_OPENING


def test_an_opening_outside_the_catalog_is_rejected() -> None:
    with pytest.raises(ValueError, match="opening"):
        generate_stream(STREAM_SEED, STREAM_LENGTH, opening=("not_a_problem",))


def test_every_repeat_and_variant_follows_its_first_occurrence() -> None:
    seen_problems: set[str] = set()
    seen_families: set[str] = set()
    for incident in _stream():
        if incident.kind == "exact":
            assert incident.problem_id in seen_problems
        elif incident.kind == "variant":
            assert incident.family in seen_families
            assert incident.problem_id not in seen_problems
        else:
            assert incident.kind in {"first", "novel"}
            assert incident.family not in seen_families
        seen_problems.add(incident.problem_id)
        seen_families.add(incident.family)


def test_the_stream_mixes_every_incident_type_and_its_pilot_prefix_already_does() -> None:
    kinds = [i.kind for i in _stream()]
    assert {"first", "exact", "variant", "novel"} <= set(kinds)
    assert kinds.count("first") == len(HOTEL_CATALOG.core)
    assert kinds.count("novel") == NOVEL_COUNT
    assert {"first", "exact", "variant"} <= {i.kind for i in _stream()[:PILOT]}
    assert "first" not in kinds[PILOT:]


def test_every_core_fault_recurs_exactly_after_its_first_occurrence() -> None:
    exact = {i.problem_id for i in _stream() if i.kind == "exact"}
    assert {family.problem_id for family in HOTEL_CATALOG.core} <= exact


def test_no_fault_is_injected_twice_in_a_row() -> None:
    ids = [i.problem_id for i in _stream()]
    assert all(a != b for a, b in zip(ids, ids[1:], strict=False))


def test_every_problem_id_is_a_known_hotel_reservation_registration() -> None:
    known = {p for family in HOTEL_CATALOG.core + HOTEL_CATALOG.novel for p in family.all_problem_ids}
    assert {i.problem_id for i in _stream()} <= known


def test_a_catalog_family_needs_a_base_problem() -> None:
    with pytest.raises(ValueError, match="problem_id"):
        FaultFamily(name="x", problem_id="", variants=())


def test_the_stream_needs_enough_variant_supply_for_its_length() -> None:
    with pytest.raises(ValueError, match="length"):
        generate_stream(STREAM_SEED, 3)


def test_the_manifest_records_index_problem_kind_and_family() -> None:
    manifest = json.loads(render_manifest(_stream()))
    assert manifest["seed"] == STREAM_SEED
    assert [row["index"] for row in manifest["incidents"]] == list(range(STREAM_LENGTH))
    assert set(manifest["incidents"][0]) == {"index", "problem_id", "kind", "family"}


def test_the_sdo_pipeline_has_one_chained_stage_per_incident_on_one_persistent_controller() -> None:
    document = tomllib.loads(render_sdo_pipeline_toml(_stream(), name="t"))
    stages = document["stages"]
    assert [s["runner"]["problems"] for s in stages] == [[i.problem_id] for i in _stream()]
    assert stages[0]["chain_application_workspace"] is False
    assert all(s["chain_application_workspace"] for s in stages[1:])
    assert document["defaults"]["agent_config"]["sdo_codex"]["persistent_controller"] is True
    assert document["defaults"]["require_strict_receipt"] is True
    assert document["defaults"]["allow_failed_verdicts"] is True


def test_the_baseline_lists_the_identical_problems_in_order() -> None:
    document = tomllib.loads(render_baseline_toml(_stream()[:PILOT]))
    assert document["runner"]["problems"] == [i.problem_id for i in _stream()[:PILOT]]
    assert document["runner"]["agent"] == "codex"


COMMITTED = {
    "sdo_codex_luna_stream.toml": (render_sdo_pipeline_toml, STREAM_LENGTH),
    "sdo_codex_luna_stream_pilot.toml": (render_sdo_pipeline_toml, PILOT),
}


@pytest.mark.parametrize("name", sorted(COMMITTED))
def test_committed_sdo_stream_configs_match_the_generator(name: str) -> None:
    render, count = COMMITTED[name]
    expected = render(
        generate_stream(STREAM_SEED, STREAM_LENGTH, opening=STREAM_OPENING)[:count], name=name.removesuffix(".toml")
    )
    assert (EXPERIMENTS / name).read_text(encoding="utf-8") == expected


def test_committed_baseline_stream_configs_match_the_generator() -> None:
    stream = _stream()
    assert (EXPERIMENTS / "codex_luna_stream_baseline_1_8.toml").read_text(encoding="utf-8") == render_baseline_toml(
        stream[:PILOT], label="incidents 1-8"
    )
    assert (EXPERIMENTS / "codex_luna_stream_baseline_5_8.toml").read_text(encoding="utf-8") == render_baseline_toml(
        stream[4:PILOT], label="incidents 5-8"
    )
    assert (EXPERIMENTS / "codex_luna_stream_baseline_9_24.toml").read_text(encoding="utf-8") == render_baseline_toml(
        stream[PILOT:], label="incidents 9-24"
    )


def test_committed_manifest_matches_the_generator() -> None:
    assert (EXPERIMENTS / "stream_learning_curve_manifest.json").read_text(encoding="utf-8") == render_manifest(
        _stream()
    )
