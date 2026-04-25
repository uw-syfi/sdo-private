"""Tests for libs.sregym_lib.experiment."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from libs.sregym_lib.experiment import (
    ExperimentConfig,
    RunnerEnv,
    VariantConfig,
    _serialize_config,
    config_to_env,
    config_to_main_args,
    load_experiment_config,
    resolve_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "exp.toml"
    p.write_text(textwrap.dedent(content))
    return p


def test_spec_names_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        spec_names = ["service_dns_resolution_failure", "wrong_dns_policy"]

        [runner.variants]
        enabled = false
    """,
    )
    config = load_experiment_config(toml)
    assert config.spec_names == ["service_dns_resolution_failure", "wrong_dns_policy"]


def test_spec_names_defaults_to_empty(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "crucible"

        [runner.variants]
        enabled = false
    """,
    )
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
    remaining = args[idx + 2 :]
    assert "--problem-spec" in remaining
    assert remaining[remaining.index("--problem-spec") + 1] == "wrong_dns_policy"


def test_config_to_main_args_no_problem_spec_when_empty(tmp_path: Path) -> None:
    config = ExperimentConfig()
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--problem-spec" not in args


def test_deploy_from_source_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        deploy_from_source = true

        [runner.variants]
        enabled = false
    """,
    )
    config = load_experiment_config(toml)
    assert config.deploy_from_source is True


def test_app_filter_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        app_filter = "hotel_reservation"

        [runner.variants]
        enabled = false
    """,
    )
    config = load_experiment_config(toml)
    assert config.app_filter == "hotel_reservation"


def test_application_workspace_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        parallel = 1
        app_filter = "hotel_reservation"
        deploy_from_source = true
        application_workspace = true

        [runner.variants]
        enabled = false
    """,
    )
    config = load_experiment_config(toml)
    assert config.application_workspace is True


def test_application_workspace_mode_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        parallel = 1
        app_filter = "hotel_reservation"
        deploy_from_source = true
        application_workspace = "ephemeral"

        [runner.variants]
        enabled = false
    """,
    )
    config = load_experiment_config(toml)
    assert config.application_workspace == "ephemeral"


def test_config_to_main_args_emits_deploy_from_source_flag(tmp_path: Path) -> None:
    config = ExperimentConfig(deploy_from_source=True)
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--deploy-from-source" in args


def test_config_to_main_args_emits_app_filter(tmp_path: Path) -> None:
    config = ExperimentConfig(app_filter="social_network")
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--app-filter" in args
    assert "social_network" in args


def test_config_to_main_args_emits_application_workspace_flag(tmp_path: Path) -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace=True,
    )
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--application-workspace" in args


def test_config_to_main_args_emits_application_workspace_mode(tmp_path: Path) -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace="ephemeral",
    )
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    idx = args.index("--application-workspace")
    assert args[idx + 1] == "ephemeral"


def test_config_to_main_args_omits_deploy_from_source_when_disabled(tmp_path: Path) -> None:
    config = ExperimentConfig()
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--deploy-from-source" not in args


def test_serialize_includes_deploy_from_source() -> None:
    config = ExperimentConfig(deploy_from_source=True)
    serialized = _serialize_config(config)
    assert "deploy_from_source = true" in serialized


def test_serialize_includes_app_filter() -> None:
    config = ExperimentConfig(app_filter="hotel_reservation")
    serialized = _serialize_config(config)
    assert 'app_filter = "hotel_reservation"' in serialized


def test_serialize_includes_application_workspace() -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace=True,
    )
    serialized = _serialize_config(config)
    assert "application_workspace = true" in serialized


def test_serialize_includes_application_workspace_mode() -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace="ephemeral",
    )
    serialized = _serialize_config(config)
    assert 'application_workspace = "ephemeral"' in serialized


def test_roundtrip_deploy_from_source(tmp_path: Path) -> None:
    config = ExperimentConfig(deploy_from_source=True)
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.deploy_from_source is True


def test_roundtrip_app_filter(tmp_path: Path) -> None:
    config = ExperimentConfig(app_filter="social_network")
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.app_filter == "social_network"


def test_roundtrip_application_workspace(tmp_path: Path) -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace=True,
    )
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.application_workspace is True


def test_roundtrip_application_workspace_mode(tmp_path: Path) -> None:
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace="ephemeral",
    )
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.application_workspace == "ephemeral"


def test_application_workspace_requires_app_filter() -> None:
    with pytest.raises(ValueError, match="application_workspace requires runner.app_filter"):
        ExperimentConfig(deploy_from_source=True, application_workspace=True)


def test_application_workspace_requires_deploy_from_source() -> None:
    with pytest.raises(ValueError, match="application_workspace requires runner.deploy_from_source = true"):
        ExperimentConfig(app_filter="hotel_reservation", application_workspace=True)


def test_application_workspace_allows_parallel_workers() -> None:
    config = ExperimentConfig(
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace=True,
        parallel=2,
    )

    assert config.parallel == 2


def test_application_workspace_mode_allows_parallel_workers() -> None:
    config = ExperimentConfig(
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace="ephemeral",
        parallel=2,
    )

    assert config.parallel == 2


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


def test_reuse_cluster_defaults_to_false() -> None:
    config = ExperimentConfig()
    assert config.env.reuse_cluster is False
    assert config.env.force_recreate_cluster is False
    assert config.env.submit_done_returns_feedback is False


def test_reuse_cluster_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "crucible"

        [runner.variants]
        enabled = false

        [runner.env]
        reuse_cluster = true
        force_recreate_cluster = false
        submit_done_returns_feedback = true
    """,
    )
    config = load_experiment_config(toml)
    assert config.env.reuse_cluster is True
    assert config.env.force_recreate_cluster is False
    assert config.env.submit_done_returns_feedback is True


def test_reuse_cluster_env_override_enables() -> None:
    config = ExperimentConfig(env=RunnerEnv(reuse_cluster=False))
    resolved = resolve_config(config, env_overrides={"SREGYM_REUSE_CLUSTER": "1"})
    assert resolved.env.reuse_cluster is True


def test_reuse_cluster_env_override_disables() -> None:
    config = ExperimentConfig(env=RunnerEnv(reuse_cluster=True))
    resolved = resolve_config(config, env_overrides={"SREGYM_REUSE_CLUSTER": "false"})
    assert resolved.env.reuse_cluster is False


def test_force_recreate_env_override() -> None:
    config = ExperimentConfig(env=RunnerEnv(reuse_cluster=True))
    resolved = resolve_config(config, env_overrides={"SREGYM_FORCE_RECREATE_CLUSTER": "yes"})
    assert resolved.env.reuse_cluster is True
    assert resolved.env.force_recreate_cluster is True


def test_submit_done_feedback_env_override() -> None:
    config = ExperimentConfig(env=RunnerEnv(submit_done_returns_feedback=False))
    resolved = resolve_config(config, env_overrides={"SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK": "1"})
    assert resolved.env.submit_done_returns_feedback is True


def test_deploy_from_source_env_override_enables() -> None:
    config = ExperimentConfig(deploy_from_source=False)
    resolved = resolve_config(config, env_overrides={"SREGYM_DEPLOY_FROM_SOURCE": "1"})
    assert resolved.deploy_from_source is True


def test_deploy_from_source_env_override_disables() -> None:
    config = ExperimentConfig(deploy_from_source=True)
    resolved = resolve_config(config, env_overrides={"SREGYM_DEPLOY_FROM_SOURCE": "false"})
    assert resolved.deploy_from_source is False


def test_config_to_env_emits_reuse_flags(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(reuse_cluster=True, force_recreate_cluster=True))
    env = config_to_env(config, project_root=tmp_path)
    assert env["SREGYM_REUSE_CLUSTER"] == "1"
    assert env["SREGYM_FORCE_RECREATE_CLUSTER"] == "1"
    assert env["SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK"] == "0"


def test_config_to_env_emits_submit_done_feedback_flag_when_enabled(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(submit_done_returns_feedback=True))
    env = config_to_env(config, project_root=tmp_path)
    assert env["SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK"] == "1"


def test_config_to_env_omits_reuse_flags_when_false(tmp_path: Path) -> None:
    config = ExperimentConfig()
    env = config_to_env(config, project_root=tmp_path)
    assert "SREGYM_REUSE_CLUSTER" not in env
    assert "SREGYM_FORCE_RECREATE_CLUSTER" not in env


def test_config_to_env_promotes_autonomous_submit_flag(tmp_path: Path) -> None:
    """[agent.cli_agent] autonomous_submit = true must surface as an env var
    so that the MCP server (launched in the worker before the driver) can
    register submit_diagnosis / submit_mitigation instead of submit."""
    config = ExperimentConfig(
        agent="cli_agent",
        agent_config={"cli_agent": {"autonomous_submit": True}},
    )
    env = config_to_env(config, project_root=tmp_path)
    assert env["SREGYM_AUTONOMOUS_SUBMIT"] == "1"


def test_config_to_env_omits_autonomous_submit_when_disabled(tmp_path: Path) -> None:
    config = ExperimentConfig(
        agent="cli_agent",
        agent_config={"cli_agent": {"autonomous_submit": False}},
    )
    env = config_to_env(config, project_root=tmp_path)
    assert "SREGYM_AUTONOMOUS_SUBMIT" not in env


def test_config_to_env_omits_autonomous_submit_when_absent(tmp_path: Path) -> None:
    config = ExperimentConfig(
        agent="cli_agent",
        agent_config={"cli_agent": {}},
    )
    env = config_to_env(config, project_root=tmp_path)
    assert "SREGYM_AUTONOMOUS_SUBMIT" not in env


def test_roundtrip_reuse_cluster(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(reuse_cluster=True))
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.env.reuse_cluster is True
    assert loaded.env.force_recreate_cluster is False


def test_roundtrip_submit_done_returns_feedback(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(submit_done_returns_feedback=True))
    toml_path = tmp_path / "snap.toml"
    toml_path.write_text(_serialize_config(config))
    loaded = load_experiment_config(toml_path)
    assert loaded.env.submit_done_returns_feedback is True
