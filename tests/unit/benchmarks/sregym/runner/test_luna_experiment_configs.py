"""The luna variants, sequence, persistent, fresh, and repeat experiments reuse the luna reuse settings.

The variants and sequence pipelines run in persistent-controller mode (one
long-running controller per application), so they copy the persistent reuse
config's defaults; that config differs from the per-problem reuse config only
in ``persistent_controller``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from benchmarks.sregym.runner.experiment import load_experiment_config
from benchmarks.sregym.runner.pipeline import load_pipeline_config, merge_stage_config

EXPERIMENTS = Path(__file__).resolve().parents[5] / "benchmarks" / "sregym" / "experiments"

ORIGINAL = "missing_configmap_hotel_reservation"
NETWORK_POLICY_BLOCK = "network_policy_block"
VARIANTS = [
    ORIGINAL,
    "missing_configmap_mongodb_rate_hotel_reservation",
    "missing_configmap_mongodb_geo_rate_hotel_reservation",
]
FOUR_FAULTS = [
    "readiness_probe_misconfiguration_hotel_reservation",
    ORIGINAL,
    "wrong_service_selector_hotel_reservation",
    NETWORK_POLICY_BLOCK,
]
PIPELINES = {
    "sdo_codex_luna_variants.toml": VARIANTS,
    "sdo_codex_luna_sequence.toml": FOUR_FAULTS + FOUR_FAULTS,
    "sdo_codex_luna_network_policy_block.toml": [NETWORK_POLICY_BLOCK],
}
BASELINES = {
    "codex_luna_variants_baseline.toml": (VARIANTS, 1),
    "codex_luna_sequence_baseline.toml": (FOUR_FAULTS + FOUR_FAULTS, 1),
    "codex_luna_baseline_x5.toml": ([ORIGINAL], 5),
    "codex_luna_network_policy_block_baseline.toml": ([NETWORK_POLICY_BLOCK], 3),
}


def _toml(name: str) -> dict:
    return tomllib.loads((EXPERIMENTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "problems"), sorted(PIPELINES.items()))
def test_pipeline_copies_luna_persistent_defaults_and_chains_one_hotel_workspace(
    name: str, problems: list[str]
) -> None:
    assert _toml(name)["defaults"] == _toml("sdo_codex_luna_persistent.toml")["defaults"]

    config = load_pipeline_config(EXPERIMENTS / name)
    resolved = [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]

    assert [stage.problems for stage in resolved] == [[problem] for problem in problems]
    assert all(stage.agent == "sdo_codex" and stage.model == "gpt-6-luna" for stage in resolved)
    assert all(stage.app_filter == "hotel_reservation" and stage.deploy_from_source for stage in resolved)
    assert all(stage.env.judge_model_id == "codex-gpt-6-luna" for stage in resolved)
    assert all(stage.agent_config["sdo_codex"]["persistent_controller"] is True for stage in resolved)
    assert [stage.chain_application_workspace for stage in config.stages] == [False] + [True] * (len(problems) - 1)
    assert not any(stage.chain_kb for stage in config.stages)


@pytest.mark.parametrize(("name", "selection"), sorted(BASELINES.items()))
def test_codex_baseline_differs_from_luna_baseline_only_in_problems_and_repeat(
    name: str, selection: tuple[list[str], int]
) -> None:
    problems, repeat = selection
    baseline = _toml(name)
    reference = _toml("codex_luna_baseline.toml")

    assert baseline["runner"].pop("problems") == problems
    assert baseline["runner"].pop("repeat", 1) == repeat
    reference["runner"].pop("problems")
    assert baseline == reference
    config = load_experiment_config(EXPERIMENTS / name)
    assert config.agent == "codex"
    assert config.problems == problems
    assert config.repeat == repeat
    assert config.env.judge_model_id == "codex-gpt-6-luna"


def test_persistent_reuse_differs_from_luna_reuse_only_in_persistent_controller() -> None:
    persistent = _toml("sdo_codex_luna_persistent.toml")
    reference = _toml("sdo_codex_luna_reuse.toml")

    assert persistent["defaults"]["agent_config"]["sdo_codex"].pop("persistent_controller") is True
    assert persistent["pipeline"].pop("name") == "sdo-codex-luna-persistent"
    reference["pipeline"].pop("name")
    assert persistent == reference


@pytest.mark.parametrize(
    ("name", "reference_name", "pipeline_name", "persistent"),
    [
        ("sdo_codex_luna_reuse_fresh.toml", "sdo_codex_luna_reuse.toml", "sdo-codex-luna-reuse-fresh", False),
        (
            "sdo_codex_luna_persistent_fresh.toml",
            "sdo_codex_luna_persistent.toml",
            "sdo-codex-luna-persistent-fresh",
            True,
        ),
    ],
)
def test_fresh_reflection_arm_differs_from_its_reference_only_in_reflection_session(
    name: str, reference_name: str, pipeline_name: str, persistent: bool
) -> None:
    fresh = _toml(name)
    reference = _toml(reference_name)

    assert fresh["defaults"]["agent_config"]["sdo_codex"].pop("reflection_session") == "fresh"
    assert fresh["pipeline"].pop("name") == pipeline_name
    reference["pipeline"].pop("name")
    assert fresh == reference

    config = load_pipeline_config(EXPERIMENTS / name)
    resolved = [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]
    assert all(stage.env.judge_model_id == "codex-gpt-6-luna" for stage in resolved)
    assert all(stage.agent_config["sdo_codex"]["reflection_session"] == "fresh" for stage in resolved)
    assert all(
        bool(stage.agent_config["sdo_codex"].get("persistent_controller", False)) is persistent for stage in resolved
    )


VERIFY_BASELINES = {
    "codex_luna_verify_baseline.toml": "codex_luna_baseline_x5.toml",
    "codex_luna_verify_sequence_baseline.toml": "codex_luna_sequence_baseline.toml",
    "codex_luna_verify_network_policy_block_baseline.toml": "codex_luna_network_policy_block_baseline.toml",
}


@pytest.mark.parametrize(("name", "base_name"), sorted(VERIFY_BASELINES.items()))
def test_codex_verify_arm_differs_from_its_base_only_in_the_protocol_flag(name: str, base_name: str) -> None:
    """The "Codex + verify" arms isolate the (full) verify protocol; name and problems may differ.

    The base config now pins ``verify_protocol = "none"`` explicitly, since the
    Codex baseline defaults to ``"concise"`` (2026-09-28); without that pin it
    would no longer be a stock baseline.
    """
    verify = _toml(name)
    base = _toml(base_name)

    assert verify.pop("agent") == {"codex": {"verify_protocol": True}}
    assert base.pop("agent") == {"codex": {"verify_protocol": "none"}}
    for raw in (verify, base):
        raw["runner"].pop("problems")
        raw["runner"].pop("name", None)
    assert verify == base

    config = load_experiment_config(EXPERIMENTS / name)
    assert config.agent == "codex"
    assert config.reasoning_effort == "medium"
    assert config.env.judge_model_id == "codex-gpt-6-luna"
    assert config.agent_config["codex"]["verify_protocol"] is True


LUNA_CONFIGS = sorted(path.name for path in EXPERIMENTS.glob("*luna*.toml"))


@pytest.mark.parametrize("name", LUNA_CONFIGS)
def test_every_luna_arm_runs_on_a_one_worker_kind_cluster(name: str) -> None:
    """SDO and baseline arms share one lane topology: 1 control plane + 1 worker."""
    if "stages" in _toml(name):
        config = load_pipeline_config(EXPERIMENTS / name)
        resolved = [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]
        assert all(stage.env.kind_worker_nodes == 1 for stage in resolved)
    else:
        assert load_experiment_config(EXPERIMENTS / name).env.kind_worker_nodes == 1
