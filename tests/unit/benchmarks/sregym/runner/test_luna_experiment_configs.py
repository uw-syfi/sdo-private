"""The luna variants and sequence experiments reuse the luna reuse settings exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from benchmarks.sregym.runner.experiment import load_experiment_config
from benchmarks.sregym.runner.pipeline import load_pipeline_config, merge_stage_config

EXPERIMENTS = Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "experiments"

ORIGINAL = "missing_configmap_hotel_reservation"
VARIANTS = [
    ORIGINAL,
    "missing_configmap_mongodb_rate_hotel_reservation",
    "missing_configmap_mongodb_geo_rate_hotel_reservation",
]
FOUR_FAULTS = [
    "readiness_probe_misconfiguration_hotel_reservation",
    ORIGINAL,
    "wrong_service_selector_hotel_reservation",
    "network_policy_block",
]
PIPELINES = {
    "sdo_codex_luna_variants.toml": VARIANTS,
    "sdo_codex_luna_sequence.toml": FOUR_FAULTS + FOUR_FAULTS,
}
BASELINES = {
    "codex_luna_variants_baseline.toml": VARIANTS,
    "codex_luna_sequence_baseline.toml": FOUR_FAULTS,
}


def _toml(name: str) -> dict:
    return tomllib.loads((EXPERIMENTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "problems"), sorted(PIPELINES.items()))
def test_pipeline_copies_luna_defaults_and_chains_one_hotel_workspace(name: str, problems: list[str]) -> None:
    assert _toml(name)["defaults"] == _toml("sdo_codex_luna_reuse.toml")["defaults"]

    config = load_pipeline_config(EXPERIMENTS / name)
    resolved = [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]

    assert [stage.problems for stage in resolved] == [[problem] for problem in problems]
    assert all(stage.agent == "sdo_codex" and stage.model == "gpt-6-luna" for stage in resolved)
    assert all(stage.app_filter == "hotel_reservation" and stage.deploy_from_source for stage in resolved)
    assert all(stage.env.judge_model_id == "codex-gpt-6-luna" for stage in resolved)
    assert [stage.chain_application_workspace for stage in config.stages] == [False] + [True] * (len(problems) - 1)
    assert not any(stage.chain_kb for stage in config.stages)


@pytest.mark.parametrize(("name", "problems"), sorted(BASELINES.items()))
def test_codex_baseline_differs_from_luna_baseline_only_in_problems(name: str, problems: list[str]) -> None:
    baseline = _toml(name)
    reference = _toml("codex_luna_baseline.toml")

    assert baseline["runner"].pop("problems") == problems
    reference["runner"].pop("problems")
    assert baseline == reference
    config = load_experiment_config(EXPERIMENTS / name)
    assert config.agent == "codex"
    assert config.problems == problems
