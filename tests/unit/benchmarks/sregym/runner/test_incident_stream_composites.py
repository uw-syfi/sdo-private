"""The mixed incident stream: single faults and multi-fault composites in one deterministic stream."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import tomllib

from benchmarks.sregym.runner.incident_stream import (
    HOTEL_CATALOG,
    MINI_OPENING,
    MIXED_CATALOG,
    MIXED_LENGTH,
    MIXED_PILOT_LENGTH,
    MIXED_SEED,
    FaultFamily,
    StreamIncident,
    generate_stream,
    mini_stream,
    render_baseline_toml,
    render_manifest,
    render_sdo_pipeline_toml,
)

ROOT = Path(__file__).resolve().parents[5]
EXPERIMENTS = ROOT / "benchmarks" / "sregym" / "experiments"
COMPOSED_FAILURES = ROOT / "third_party" / "sregym" / "sregym" / "conductor" / "problems" / "composed_failures.py"


def _stream() -> list[StreamIncident]:
    return generate_stream(MIXED_SEED, MIXED_LENGTH, MIXED_CATALOG)


def _composite_ids() -> set[str]:
    return {p for family in MIXED_CATALOG.composite for p in family.all_problem_ids}


def test_the_mixed_stream_is_deterministic_and_the_seed_matters() -> None:
    assert _stream() == _stream()
    assert [i.problem_id for i in generate_stream(MIXED_SEED + 1, MIXED_LENGTH, MIXED_CATALOG)] != [
        i.problem_id for i in _stream()
    ]


def test_a_catalog_without_composites_is_unchanged() -> None:
    assert HOTEL_CATALOG.composite == ()
    assert "composite" not in {i.kind for i in generate_stream(20260930, 24)}


def test_a_composite_family_must_name_known_component_families() -> None:
    with pytest.raises(ValueError, match="requires"):
        type(MIXED_CATALOG)(
            core=MIXED_CATALOG.core,
            novel=MIXED_CATALOG.novel,
            composite=(FaultFamily("c", "c_problem", (), requires=("not-a-core-family",)),),
        )


def test_every_composite_follows_a_single_occurrence_of_each_of_its_component_classes() -> None:
    required = {f.name: set(f.requires) for f in MIXED_CATALOG.composite}
    seen_singles: set[str] = set()
    for incident in _stream():
        if incident.family in required:
            assert required[incident.family] <= seen_singles, incident
        else:
            seen_singles.add(incident.family)


def test_the_first_occurrence_of_a_composite_family_has_the_composite_kind() -> None:
    first_by_family: dict[str, StreamIncident] = {}
    for incident in _stream():
        first_by_family.setdefault(incident.family, incident)
    for family in MIXED_CATALOG.composite:
        assert first_by_family[family.name].kind == "composite"
        assert first_by_family[family.name].problem_id == family.problem_id


def test_the_stream_has_composite_repeats_and_variants_and_single_ones() -> None:
    stream = _stream()
    composite_families = {f.name for f in MIXED_CATALOG.composite}
    composites = [i for i in stream if i.family in composite_families]
    assert 8 <= len(composites) <= 10
    assert any(i.kind == "exact" for i in composites)
    assert any(i.kind == "variant" for i in composites)
    singles = [i for i in stream if i.family not in composite_families]
    assert {"first", "novel", "exact", "variant"} <= {i.kind for i in singles}


def test_the_mixed_pilot_prefix_holds_every_core_first_a_composite_a_repeat_and_a_variant() -> None:
    kinds = [i.kind for i in _stream()[:MIXED_PILOT_LENGTH]]
    assert kinds.count("first") == len(MIXED_CATALOG.core)
    assert {"composite", "exact", "variant"} <= set(kinds)


def test_no_fault_is_injected_twice_in_a_row_and_repeats_follow_their_first_occurrence() -> None:
    stream = _stream()
    assert all(a.problem_id != b.problem_id for a, b in zip(stream, stream[1:], strict=False))
    seen: set[str] = set()
    seen_families: set[str] = set()
    for incident in stream:
        if incident.kind == "exact":
            assert incident.problem_id in seen
        elif incident.kind == "variant":
            assert incident.family in seen_families
            assert incident.problem_id not in seen
        else:
            assert incident.family not in seen_families
        seen.add(incident.problem_id)
        seen_families.add(incident.family)


def test_an_opening_that_runs_a_composite_before_its_components_is_rejected() -> None:
    with pytest.raises(ValueError, match="component"):
        generate_stream(MIXED_SEED, MIXED_LENGTH, MIXED_CATALOG, opening=("composite3c_hotel_rate_mongodb_geo_user",))


def test_the_mini_stream_runs_three_singles_then_a_composite_its_repeat_and_a_variant() -> None:
    mini = mini_stream()
    assert tuple(i.problem_id for i in mini[: len(MINI_OPENING)]) == MINI_OPENING
    assert [i.kind for i in mini] == ["first", "first", "first", "composite", "exact", "variant"]
    assert mini[3].problem_id == mini[4].problem_id == "composite3c_hotel_rate_mongodb_geo_user"
    assert mini[5].family == mini[3].family
    assert mini[5].problem_id != mini[3].problem_id


def test_every_composite_problem_is_registered_in_the_pinned_sregym_checkout() -> None:
    if not COMPOSED_FAILURES.exists():
        pytest.skip("third_party/sregym is not populated")
    registered = set(re.findall(r'^    "(composite[^"]+)": \(', COMPOSED_FAILURES.read_text(), re.MULTILINE))
    assert _composite_ids() <= registered


def test_the_composites_use_only_fault_classes_that_exist_as_singles() -> None:
    """composite5 adds an oversized resource request that has no usable single fault, so it is not in the stream."""

    assert "composite5_hotel_geo_rate_recommendation_frontend_user" not in _composite_ids()


def test_the_manifest_and_pipeline_carry_the_composite_kind() -> None:
    manifest = json.loads(render_manifest(_stream(), MIXED_SEED))
    assert {"first", "novel", "exact", "variant", "composite"} == {row["kind"] for row in manifest["incidents"]}
    document = tomllib.loads(render_sdo_pipeline_toml(_stream(), name="t", seed=MIXED_SEED))
    assert [s["runner"]["problems"] for s in document["stages"]] == [[i.problem_id] for i in _stream()]


COMMITTED = {
    "sdo_codex_luna_mixed_stream.toml": lambda: render_sdo_pipeline_toml(
        _stream(), name="sdo_codex_luna_mixed_stream", seed=MIXED_SEED
    ),
    "sdo_codex_luna_mixed_pilot.toml": lambda: render_sdo_pipeline_toml(
        _stream()[:MIXED_PILOT_LENGTH], name="sdo_codex_luna_mixed_pilot", seed=MIXED_SEED
    ),
    "sdo_codex_luna_mixed_mini.toml": lambda: render_sdo_pipeline_toml(
        mini_stream(), name="sdo_codex_luna_mixed_mini", seed=MIXED_SEED
    ),
    "codex_luna_mixed_baseline.toml": lambda: render_baseline_toml(_stream(), label="mixed stream", seed=MIXED_SEED),
    "codex_luna_mixed_baseline_mini.toml": lambda: render_baseline_toml(
        mini_stream(), label="mixed mini stream", seed=MIXED_SEED
    ),
    "mixed_stream_manifest.json": lambda: render_manifest(_stream(), MIXED_SEED),
}


@pytest.mark.parametrize("name", sorted(COMMITTED))
def test_committed_mixed_configs_match_the_generator(name: str) -> None:
    assert (EXPERIMENTS / name).read_text(encoding="utf-8") == COMMITTED[name]()
