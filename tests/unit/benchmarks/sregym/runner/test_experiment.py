"""Tests for benchmarks.sregym.runner.experiment."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

import pytest
import yaml

from benchmarks.sregym.runner.experiment import (
    ExperimentConfig,
    RunnerEnv,
    VariantConfig,
    _serialize_config,
    config_to_env,
    config_to_main_args,
    load_experiment_config,
    resolve_config,
    resolve_tasklist,
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


def test_config_to_main_args_emits_agent_timeout(tmp_path: Path) -> None:
    config = ExperimentConfig(agent_timeout=3600)

    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)

    index = args.index("--agent-timeout")
    assert args[index + 1] == "3600"


def test_config_to_main_args_emits_explicit_judge_model(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(judge_model_id="vertex-ai-gemini-2.5-pro"))

    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)

    index = args.index("--judge-model")
    assert args[index + 1] == "vertex-ai-gemini-2.5-pro"


def test_config_to_main_args_does_not_emit_crucible_summary_flags(tmp_path: Path) -> None:
    config = ExperimentConfig(
        agent="crucible",
        agent_config={"crucible": {"enable_summary": True, "no_inject_summary": False}},
    )
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--enable-summary" not in args
    assert "--no-inject-summary" not in args


def test_crucible_agent_config_loaded_from_toml(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "crucible"

        [agent.crucible]
        enable_summary = true
        no_inject_summary = false
        seed_kb_dir = "/tmp/seed-kb"
    """,
    )
    config = load_experiment_config(toml)
    assert config.agent_config["crucible"]["enable_summary"] is True
    assert config.agent_config["crucible"]["no_inject_summary"] is False
    assert config.agent_config["crucible"]["seed_kb_dir"] == "/tmp/seed-kb"


def test_sdo_codex_exports_required_kind_images_for_prefault_preflight(tmp_path: Path) -> None:
    config = ExperimentConfig(
        agent="sdo_codex",
        agent_config={
            "sdo_codex": {
                "controller_image": "controller:run-123",
                "responder_image": "responder:run-123",
                "validator_image": "validator:run-123",
            }
        },
    )

    env = config_to_env(config, tmp_path)

    assert json.loads(env["SREGYM_KIND_REQUIRED_IMAGES"]) == [
        "controller:run-123",
        "responder:run-123",
        "validator:run-123",
    ]
    assert env["SREGYM_KIND_REQUIRE_NETWORK_POLICY"] == "1"
    assert env["SREGYM_KIND_NETWORK_POLICY_CANARY_IMAGE"] == "validator:run-123"


def test_legacy_crucible_runner_fields_are_promoted_to_agent_config(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "crucible"
        enable_summary = false
        no_inject_summary = true

        [runner.env]
        crucible_seed_kb_dir = "/tmp/legacy-kb"
        judge_model_id = "legacy-judge"
    """,
    )
    config = load_experiment_config(toml)
    assert config.agent_config["crucible"]["enable_summary"] is False
    assert config.agent_config["crucible"]["no_inject_summary"] is True
    assert config.agent_config["crucible"]["seed_kb_dir"] == "/tmp/legacy-kb"
    assert "judge_model_id" not in config.agent_config["crucible"]
    assert config.env.judge_model_id == "legacy-judge"


def test_legacy_crucible_agent_seed_field_is_renamed(tmp_path: Path) -> None:
    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "crucible"

        [agent.crucible]
        crucible_seed_kb_dir = "/tmp/old-agent-seed"
    """,
    )
    config = load_experiment_config(toml)
    assert config.agent_config["crucible"]["seed_kb_dir"] == "/tmp/old-agent-seed"
    assert "crucible_seed_kb_dir" not in config.agent_config["crucible"]


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


def test_config_to_main_args_ephemeral_emits_bare_flag_no_value(tmp_path: Path) -> None:
    """Ephemeral mode must render the BARE ``--application-workspace`` flag.

    ``main.py`` declares ``--application-workspace`` with ``action="store_true"``
    (boolean), so a trailing mode token like ``ephemeral`` is an argparse usage
    error (exit code 2) — which previously aborted the ephemeral control stage
    of a pipeline. The mode remains runner-side state and is never emitted as
    a positional value on the harness CLI.
    """
    config = ExperimentConfig(
        parallel=1,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        application_workspace="ephemeral",
    )
    args = config_to_main_args(config, exp_dir=tmp_path, tasklist_path=None)
    assert "--application-workspace" in args
    # No bare mode token may follow the flag (would break main.py argparse).
    idx = args.index("--application-workspace")
    after = args[idx + 1] if idx + 1 < len(args) else None
    assert after != "ephemeral"
    assert "ephemeral" not in args


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


def test_serialize_writes_crucible_fields_under_agent_config() -> None:
    config = ExperimentConfig(
        agent="crucible",
        agent_config={
            "crucible": {
                "enable_summary": True,
                "no_inject_summary": False,
                "seed_kb_dir": "/tmp/seed",
            }
        },
        env=RunnerEnv(judge_model_id="judge-model"),
    )
    serialized = _serialize_config(config)
    runner_block = serialized.split("[runner.variants]", 1)[0]
    assert "enable_summary" not in runner_block
    assert "no_inject_summary" not in runner_block
    assert "[agent.crucible]" in serialized
    assert "enable_summary = true" in serialized
    assert "no_inject_summary = false" in serialized
    assert 'seed_kb_dir = "/tmp/seed"' in serialized
    assert '[runner.env]\njudge_model_id = "judge-model"' in serialized


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
    assert config.env.preserve_infrastructure is False
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
        preserve_infrastructure = true
        submit_done_returns_feedback = true
    """,
    )
    config = load_experiment_config(toml)
    assert config.env.reuse_cluster is True
    assert config.env.force_recreate_cluster is False
    assert config.env.preserve_infrastructure is True
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


def test_cleanup_timeout_process_env_overrides_stale_persisted_config(tmp_path: Path) -> None:
    stale = ExperimentConfig(env=RunnerEnv(cleanup_defer_timeout_seconds=600))

    resolved = resolve_config(stale, env_overrides={"SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS": "1800"})

    assert resolved.env.cleanup_defer_timeout_seconds == 1800
    assert config_to_env(resolved, project_root=tmp_path)["SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS"] == "1800"


def test_deferred_diagnosis_grading_is_opt_in_and_reaches_the_conductor(tmp_path: Path) -> None:
    assert ExperimentConfig().env.defer_diagnosis_grading is False
    assert "SREGYM_DEFER_DIAGNOSIS_GRADING" not in config_to_env(ExperimentConfig(), project_root=tmp_path)

    toml = _write_toml(
        tmp_path,
        """
        [runner]
        agent = "sdo-codex"

        [runner.env]
        defer_diagnosis_grading = true
    """,
    )
    config = load_experiment_config(toml)

    assert config.env.defer_diagnosis_grading is True
    assert config_to_env(config, project_root=tmp_path)["SREGYM_DEFER_DIAGNOSIS_GRADING"] == "1"
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(_serialize_config(config))
    assert load_experiment_config(snapshot).env.defer_diagnosis_grading is True
    disabled = resolve_config(config, env_overrides={"SREGYM_DEFER_DIAGNOSIS_GRADING": "0"})
    assert disabled.env.defer_diagnosis_grading is False


def test_fast_namespace_teardown_is_opt_in_and_reaches_the_conductor(tmp_path: Path) -> None:
    assert "SREGYM_FAST_NAMESPACE_TEARDOWN" not in config_to_env(ExperimentConfig(), project_root=tmp_path)
    toml = _write_toml(
        tmp_path,
        """
        [runner.env]
        fast_namespace_teardown = true
    """,
    )
    config = load_experiment_config(toml)

    assert config_to_env(config, project_root=tmp_path)["SREGYM_FAST_NAMESPACE_TEARDOWN"] == "1"
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(_serialize_config(config))
    assert load_experiment_config(snapshot).env.fast_namespace_teardown is True
    disabled = resolve_config(config, env_overrides={"SREGYM_FAST_NAMESPACE_TEARDOWN": "0"})
    assert disabled.env.fast_namespace_teardown is False


def test_source_build_cache_is_opt_in_and_reaches_the_conductor(tmp_path: Path) -> None:
    assert "SREGYM_SOURCE_BUILD_CACHE" not in config_to_env(ExperimentConfig(), project_root=tmp_path)
    toml = _write_toml(
        tmp_path,
        """
        [runner.env]
        source_build_cache = true
    """,
    )
    config = load_experiment_config(toml)

    assert config_to_env(config, project_root=tmp_path)["SREGYM_SOURCE_BUILD_CACHE"] == "1"
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(_serialize_config(config))
    assert load_experiment_config(snapshot).env.source_build_cache is True
    disabled = resolve_config(config, env_overrides={"SREGYM_SOURCE_BUILD_CACHE": "0"})
    assert disabled.env.source_build_cache is False


def test_lifecycle_validation_cache_is_opt_in_and_shared_across_pipelines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDO_LIFECYCLE_VALIDATION_CACHE_DIR", raising=False)
    assert "SDO_LIFECYCLE_VALIDATION_CACHE_DIR" not in config_to_env(ExperimentConfig(), project_root=tmp_path)
    toml = _write_toml(
        tmp_path,
        """
        [runner.env]
        lifecycle_validation_cache = true
    """,
    )
    config = load_experiment_config(toml)

    env = config_to_env(config, project_root=tmp_path)
    assert env["SDO_LIFECYCLE_VALIDATION_CACHE_DIR"] == str(tmp_path / ".sdo-runtime" / "lifecycle-validation-cache")
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(_serialize_config(config))
    assert load_experiment_config(snapshot).env.lifecycle_validation_cache is True
    disabled = resolve_config(config, env_overrides={"SDO_LIFECYCLE_VALIDATION_CACHE": "0"})
    assert disabled.env.lifecycle_validation_cache is False


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


def test_config_to_env_emits_cleanup_deferral_timeout(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(cleanup_defer_timeout_seconds=1800))

    env = config_to_env(config, project_root=tmp_path)

    assert env["SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS"] == "1800"


def test_config_to_env_emits_source_docker_builder(tmp_path: Path) -> None:
    config = ExperimentConfig(env=RunnerEnv(docker_builder="sdo-example"))

    env = config_to_env(config, tmp_path)

    assert env["SREGYM_DOCKER_BUILDER"] == "sdo-example"


def test_config_to_env_omits_reuse_flags_when_false(tmp_path: Path) -> None:
    config = ExperimentConfig()
    env = config_to_env(config, project_root=tmp_path)
    assert "SREGYM_REUSE_CLUSTER" not in env
    assert "SREGYM_FORCE_RECREATE_CLUSTER" not in env


def test_config_to_env_serializes_effective_crucible_agent_config(tmp_path: Path) -> None:
    config = ExperimentConfig(
        agent="crucible",
        agent_config={
            "crucible": {
                "prompt_version": "v3",
                "enable_summary": True,
                "no_inject_summary": False,
                "seed_kb_dir": "/tmp/seed",
            }
        },
        env=RunnerEnv(judge_model_id="judge-model"),
    )
    env = config_to_env(config, project_root=tmp_path, exp_dir=tmp_path / "exp")
    decoded = json.loads(env["SREGYM_EXPERIMENT_AGENT_CONFIG"])
    assert decoded["prompt_version"] == "v3"
    assert decoded["enable_summary"] is True
    assert decoded["no_inject_summary"] is False
    assert decoded["seed_kb_dir"] == "/tmp/seed"
    assert "judge_model_id" not in decoded
    assert env["JUDGE_MODEL_ID"] == "judge-model"
    assert env["SREGYM_EXPERIMENT_DIR"] == str(tmp_path / "exp")


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


def test_inline_problems_keep_order_and_duplicates(tmp_path: Path) -> None:
    """Repeated inline problems each become a run in SREGym's tasklist."""
    path = _write_toml(
        tmp_path,
        """\
        [runner]
        problems = ["a", "b", "a", "b"]
        """,
    )
    config = load_experiment_config(path)

    tasklist = resolve_tasklist(config, tmp_path, tmp_path)

    assert tasklist is not None
    document = yaml.safe_load(tasklist.read_text())
    # SREGym's parallel runner accepts all.problems as a list of strings and
    # expands it in order; a mapping would collapse the repeated keys.
    assert document == {"all": {"problems": ["a", "b", "a", "b"]}}


def test_repeat_maps_to_sregym_n_attempts(tmp_path: Path) -> None:
    """SREGym main.py has no --repeat flag; independent attempts are --n-attempts."""
    config = ExperimentConfig(problems=["a"], repeat=5)

    args = config_to_main_args(config, tmp_path, None)

    assert "--repeat" not in args
    assert args[args.index("--n-attempts") + 1] == "5"


def test_default_repeat_emits_no_attempt_flag(tmp_path: Path) -> None:
    args = config_to_main_args(ExperimentConfig(problems=["a"]), tmp_path, None)

    assert "--n-attempts" not in args
    assert "--repeat" not in args


@pytest.mark.parametrize("repeat", [0, -1])
def test_repeat_must_be_positive(repeat: int) -> None:
    with pytest.raises(ValueError, match="repeat"):
        ExperimentConfig(problems=["a"], repeat=repeat)
