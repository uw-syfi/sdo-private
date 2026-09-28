"""Assurance phase-1 configs: the model rule, the lane topology and the run matrix (PLAN.md (d)).

Model rule (user, 2026-09-28): every SDO agent role and both Codex arms run ONLY
Codex gpt-6-luna at medium effort, and the SREGym judge is ONLY codex-gpt-6-luna
at the Codex CLI backend's default xhigh (no config may override it). The rule
also holds for every luna config, which the assurance arms were copied from.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import tomllib

from benchmarks.sregym.runner.experiment import load_experiment_config
from benchmarks.sregym.runner.pipeline import load_pipeline_config, merge_stage_config

ROOT = Path(__file__).resolve().parents[5]
EXPERIMENTS = ROOT / "benchmarks" / "sregym" / "experiments"
PHASE1 = EXPERIMENTS / "assurance" / "phase1"
SREGYM = ROOT / "third_party" / "sregym"

MODEL = "gpt-6-luna"
JUDGE = "codex-gpt-6-luna"
EFFORT = "medium"

S1 = "missing_configmap_hotel_reservation"
S2 = "wrong_service_selector_hotel_reservation"
S3 = "network_policy_block"
K1 = "composite_policy_and_rate_configmap_hotel_reservation"
K2 = "composite_frontend_selector_and_readiness_hotel_reservation"
PHASE_ONE = (S1, S2, S3, K1, K2)
ROTATIONS = {
    "a": (S1, S2, S3, K1, K2),
    "b": (S2, S3, K1, K2, S1),
    "c": (S3, K1, K2, S1, S2),
    "d": (K1, K2, S1, S2, S3),
}
SDO = {f"sdo_codex_luna_assure_p1_{rotation}.toml": rotation for rotation in ROTATIONS}
#: Phase 1 has no stock (no-verify) Codex arm (user decision, 2026-09-28): the
#: sole Codex arm is the default, concise-verify baseline, on 4 lanes.
VERIFY = (
    "codex_luna_verify_assure_p1_1.toml",
    "codex_luna_verify_assure_p1_2.toml",
    "codex_luna_verify_assure_p1_3.toml",
    "codex_luna_verify_assure_p1_4.toml",
)
LANES = {
    "sdo_codex_luna_assure_p1_a.toml": "assure-w0",
    "sdo_codex_luna_assure_p1_b.toml": "assure-w1",
    "sdo_codex_luna_assure_p1_c.toml": "assure-w2",
    "sdo_codex_luna_assure_p1_d.toml": "assure-w3",
    VERIFY[0]: "assure-w4",
    VERIFY[1]: "assure-w5",
    VERIFY[2]: "assure-w6",
    VERIFY[3]: "assure-w7",
}
LUNA = sorted(EXPERIMENTS.glob("*luna*.toml"))
RULED = sorted(PHASE1.glob("*.toml")) + LUNA


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _walk(node: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        return [item for key, value in node.items() for item in _walk(value, f"{path}.{key}" if path else key)]
    if isinstance(node, list):
        return [item for index, value in enumerate(node) for item in _walk(value, f"{path}[{index}]")]
    return [(path, node)]


def _resolved(path: Path) -> list:
    config = load_pipeline_config(path)
    return [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]


def test_phase_one_has_exactly_the_eight_lane_configs() -> None:
    assert sorted(path.name for path in PHASE1.glob("*.toml")) == sorted(LANES)


@pytest.mark.parametrize("path", RULED, ids=lambda path: path.name)
def test_every_model_is_luna_every_effort_is_medium_and_the_judge_is_never_overridden(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "JUDGE_REASONING_EFFORT" not in text
    for key, value in _walk(_toml(path)):
        leaf = key.rsplit(".", 1)[-1].lower()
        if leaf == "judge_model_id":
            assert value == JUDGE, key
        elif "model" in leaf:
            assert value == MODEL, key
        if "effort" in leaf:
            assert value == EFFORT, key
        if leaf == "provider":
            assert value == "codex", key


@pytest.mark.parametrize("name", sorted(LANES), ids=str)
def test_each_config_runs_on_its_own_one_worker_assurance_lane(name: str) -> None:
    path = PHASE1 / name
    header = path.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert re.findall(r"lane (assure-w\d)", header) == [LANES[name]]
    if name in SDO:
        stages = _resolved(path)
        assert all(stage.env.kind_worker_nodes == 1 for stage in stages)
        assert all(stage.env.worker_cpu_limit == "3" for stage in stages)
        assert all(stage.env.judge_model_id == JUDGE for stage in stages)
    else:
        config = load_experiment_config(path)
        assert config.env.kind_worker_nodes == 1
        assert config.env.worker_cpu_limit == "3"
        assert config.env.judge_model_id == JUDGE
    assert len(set(LANES.values())) == len(LANES)


@pytest.mark.parametrize(("name", "rotation"), sorted(SDO.items()))
def test_each_sdo_pipeline_runs_its_rotation_twice_on_one_persistent_controller(name: str, rotation: str) -> None:
    config = load_pipeline_config(PHASE1 / name)
    stages = _resolved(PHASE1 / name)

    assert [stage.problems for stage in stages] == [[problem] for problem in ROTATIONS[rotation] * 2]
    assert [stage.chain_application_workspace for stage in config.stages] == [False] + [True] * 9
    assert not any(stage.chain_kb for stage in config.stages)
    for stage in stages:
        assert stage.agent == "sdo_codex"
        assert stage.model == MODEL
        assert stage.reasoning_effort == EFFORT
        assert stage.app_filter == "hotel_reservation"
        assert stage.deploy_from_source
        sdo = stage.agent_config["sdo_codex"]
        assert sdo["provider"] == "codex"
        assert sdo["model"] == MODEL
        assert sdo["persistent_controller"] is True


def test_the_rotations_put_every_problem_once_at_every_stream_position() -> None:
    """Four cyclic rotations: at each position the four pipelines run four different problems."""
    for position in range(len(PHASE_ONE)):
        seen = [order[position] for order in ROTATIONS.values()]
        assert len(set(seen)) == len(seen)
    assert all(sorted(order) == sorted(PHASE_ONE) for order in ROTATIONS.values())
    positions = Counter((problem, order.index(problem)) for order in ROTATIONS.values() for problem in order)
    assert all(count == 1 for count in positions.values())


def test_the_codex_arm_makes_five_attempts_per_problem_over_four_interleaved_lanes() -> None:
    configs = [load_experiment_config(PHASE1 / name) for name in VERIFY]

    assert [len(config.problems) for config in configs] == [7, 6, 6, 6]
    assert Counter(problem for config in configs for problem in config.problems) == dict.fromkeys(PHASE_ONE, 5)
    assert configs[0].problems[:5] != configs[1].problems[:5]
    for config in configs:
        assert config.agent == "codex"
        assert config.model == MODEL
        assert config.reasoning_effort == EFFORT
        assert config.repeat == 1
        assert config.app_filter == "hotel_reservation"
        assert config.deploy_from_source
        assert config.agent_config["codex"]["verify_protocol"] == "concise"


def test_the_codex_arm_is_the_default_concise_verify_baseline_not_stock_or_full() -> None:
    """Phase 1 has no stock arm (user decision, 2026-09-28): every Codex lane runs the default."""
    for name in VERIFY:
        config = load_experiment_config(PHASE1 / name)
        assert config.agent_config["codex"] == {"verify_protocol": "concise"}


def test_every_phase_one_problem_is_registered_in_the_sregym_submodule() -> None:
    registry = SREGYM / "sregym" / "conductor" / "problems" / "registry.py"
    specs = SREGYM / "sregym" / "conductor" / "problems" / "composite_specs.json"
    if not registry.is_file() or not specs.is_file():
        pytest.skip("SREGym submodule is not checked out at a commit with composite problems")
    registered = registry.read_text(encoding="utf-8")
    composites = json.loads(specs.read_text(encoding="utf-8"))
    for problem in (S1, S2, S3):
        assert f'"{problem}":' in registered
    for problem in (K1, K2):
        assert problem in composites
