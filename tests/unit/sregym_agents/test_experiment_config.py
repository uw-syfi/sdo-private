"""Tests for sregym_agents.experiment_config."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from sregym_agents.experiment_config import (
    ExperimentConfig,
    VariantConfig,
    _serialize_config,
    config_to_main_args,
    load_experiment_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "exp.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_spec_names_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, """
        [runner]
        spec_names = ["service_dns_resolution_failure", "wrong_dns_policy"]

        [runner.variants]
        enabled = false
    """)
    config = load_experiment_config(toml)
    assert config.spec_names == ["service_dns_resolution_failure", "wrong_dns_policy"]


def test_spec_names_defaults_to_empty(tmp_path: Path) -> None:
    toml = _write_toml(tmp_path, """
        [runner]
        agent = "crucible"

        [runner.variants]
        enabled = false
    """)
    config = load_experiment_config(toml)
    assert config.spec_names == []


def test_spec_names_mutually_exclusive_with_variants() -> None:
    with pytest.raises(ValueError, match="runner.spec_names cannot be used with runner.variants.enabled"):
        ExperimentConfig(
            spec_names=["service_dns_resolution_failure"],
            variants=VariantConfig(enabled=True, count=5),
        )


def test_spec_names_mutually_exclusive_with_tasklist() -> None:
    with pytest.raises(ValueError, match="runner.spec_names is mutually exclusive"):
        ExperimentConfig(
            spec_names=["service_dns_resolution_failure"],
            tasklist="count_train",
        )


def test_spec_names_mutually_exclusive_with_problems() -> None:
    with pytest.raises(ValueError, match="runner.spec_names is mutually exclusive"):
        ExperimentConfig(
            spec_names=["service_dns_resolution_failure"],
            problems=["some_problem"],
        )


def test_spec_names_allowed_alone() -> None:
    config = ExperimentConfig(spec_names=["service_dns_resolution_failure"])
    assert config.spec_names == ["service_dns_resolution_failure"]


def test_config_to_main_args_emits_problem_spec(tmp_path: Path) -> None:
    config = ExperimentConfig(
        spec_names=["service_dns_resolution_failure", "wrong_dns_policy"],
    )
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--problem-spec" in args
    idx = args.index("--problem-spec")
    assert args[idx + 1] == "service_dns_resolution_failure"
    remaining = args[idx + 2:]
    assert "--problem-spec" in remaining
    assert remaining[remaining.index("--problem-spec") + 1] == "wrong_dns_policy"


def test_config_to_main_args_no_problem_spec_when_empty(tmp_path: Path) -> None:
    config = ExperimentConfig()
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--problem-spec" not in args


def test_serialize_includes_spec_names(tmp_path: Path) -> None:
    config = ExperimentConfig(spec_names=["service_dns_resolution_failure"])
    serialized = _serialize_config(config)
    assert 'spec_names = ["service_dns_resolution_failure"]' in serialized


def test_serialize_omits_spec_names_when_empty() -> None:
    config = ExperimentConfig()
    serialized = _serialize_config(config)
    assert "spec_names" not in serialized


def test_roundtrip_spec_names(tmp_path: Path) -> None:
    config = ExperimentConfig(spec_names=["service_dns_resolution_failure", "wrong_dns_policy"])
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.spec_names == config.spec_names
