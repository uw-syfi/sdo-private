"""Tests for libs.sregym_lib.pipeline."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.sregym_lib.pipeline import (
    PipelineConfig,
    PipelineState,
    StageConfig,
    StageState,
    _deep_merge,
    has_pipeline_state,
    is_pipeline_config,
    load_pipeline_config,
    merge_stage_config,
    read_pipeline_state,
    reset_stages_for_rerun,
    write_pipeline_snapshot,
    write_pipeline_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str, name: str = "test.toml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


PIPELINE_TOML = """\
[pipeline]
name = "test-pipeline"

[defaults]
agent = "crucible"
model = "gemini-flash"
parallel = 4
app_filter = "hotel_reservation"
deploy_from_source = true
application_workspace = true
spec_names = ["wrong_service_selector"]

[defaults.env]
judge_model_id = "judge-model"
reuse_cluster = true
force_recreate_cluster = false
submit_done_returns_feedback = true

[defaults.variants]
seed = 99

[[stages]]
name = "build_kb"
chain_kb = false
chain_application_workspace = false

[stages.runner]
parallel = 8

[stages.runner.variants]
enabled = true
count = 16

[[stages]]
name = "evaluate"
chain_kb = true
chain_application_workspace = true

[stages.runner]
tasklist = "count_train"
"""

SINGLE_EXPERIMENT_TOML = """\
[runner]
agent = "crucible"
model = "gemini-flash"
parallel = 4

[runner.variants]
enabled = true
count = 8

[runner.env]
judge_model_id = "judge-model"
crucible_seed_kb_dir = ""
worker_cpu_limit = "16"

[agent.crucible]
enable_judge = true
"""


# ---------------------------------------------------------------------------
# test_load_pipeline_config
# ---------------------------------------------------------------------------


class TestLoadPipelineConfig:
    def test_parse_two_stage_pipeline(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, PIPELINE_TOML)
        config = load_pipeline_config(p)

        assert config.name == "test-pipeline"
        assert len(config.stages) == 2
        assert config.stages[0].name == "build_kb"
        assert config.stages[0].chain_kb is False
        assert config.stages[0].chain_application_workspace is False
        assert config.stages[0].runner_overrides["parallel"] == 8
        assert config.stages[0].runner_overrides["variants"]["enabled"] is True
        assert config.stages[1].name == "evaluate"
        assert config.stages[1].chain_kb is True
        assert config.stages[1].chain_application_workspace is True
        assert config.stages[1].runner_overrides["tasklist"] == "count_train"
        assert config.defaults["model"] == "gemini-flash"
        assert config.defaults["app_filter"] == "hotel_reservation"
        assert config.defaults["env"]["judge_model_id"] == "judge-model"

    def test_no_stages_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one stage"):
            PipelineConfig(name="empty", stages=[])

    def test_duplicate_stage_names_raises(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            PipelineConfig(
                name="dup",
                stages=[
                    StageConfig(name="a"),
                    StageConfig(name="a"),
                ],
            )


# ---------------------------------------------------------------------------
# test_is_pipeline_config
# ---------------------------------------------------------------------------


class TestIsPipelineConfig:
    def test_pipeline_toml_detected(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, PIPELINE_TOML)
        assert is_pipeline_config(p) is True

    def test_single_experiment_not_detected(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, SINGLE_EXPERIMENT_TOML)
        assert is_pipeline_config(p) is False


# ---------------------------------------------------------------------------
# test_merge_stage_config
# ---------------------------------------------------------------------------


class TestMergeStageConfig:
    def test_defaults_only(self) -> None:
        defaults = {
            "agent": "crucible",
            "model": "gemini-flash",
            "parallel": 1,
            "app_filter": "hotel_reservation",
            "deploy_from_source": True,
            "application_workspace": True,
            "spec_names": ["wrong_service_selector"],
            "variants": {"seed": 99},
            "env": {"judge_model_id": "judge", "reuse_cluster": True, "force_recreate_cluster": False},
        }
        config = merge_stage_config(defaults, {})
        assert config.agent == "crucible"
        assert config.model == "gemini-flash"
        assert config.parallel == 1
        assert config.app_filter == "hotel_reservation"
        assert config.deploy_from_source is True
        assert config.application_workspace is True
        assert config.spec_names == ["wrong_service_selector"]
        assert config.variants.seed == 99
        assert config.env.judge_model_id == "judge"
        assert config.env.reuse_cluster is True
        assert config.env.force_recreate_cluster is False
        assert config.env.submit_done_returns_feedback is False

    def test_defaults_env_preserves_submit_done_feedback(self) -> None:
        defaults = {
            "env": {
                "judge_model_id": "judge",
                "reuse_cluster": True,
                "force_recreate_cluster": False,
                "submit_done_returns_feedback": True,
            },
        }
        config = merge_stage_config(defaults, {})
        assert config.env.submit_done_returns_feedback is True

    def test_with_overrides(self) -> None:
        defaults = {
            "agent": "crucible",
            "model": "gemini-flash",
            "parallel": 4,
            "variants": {"seed": 99, "enabled": False},
        }
        overrides = {"model": "gemini-pro", "parallel": 8}
        config = merge_stage_config(defaults, overrides)
        assert config.model == "gemini-pro"
        assert config.parallel == 8
        assert config.agent == "crucible"  # inherited

    def test_workspace_related_fields_are_preserved(self) -> None:
        defaults = {
            "agent": "cli_agent",
            "parallel": 1,
            "app_filter": "hotel_reservation",
            "deploy_from_source": True,
            "application_workspace": True,
            "spec_names": ["wrong_service_selector"],
            "env": {
                "reuse_cluster": True,
                "force_recreate_cluster": True,
                "submit_done_returns_feedback": True,
            },
        }
        overrides = {"model": "claude-sonnet-4-5"}
        config = merge_stage_config(defaults, overrides)
        assert config.agent == "cli_agent"
        assert config.model == "claude-sonnet-4-5"
        assert config.parallel == 1
        assert config.app_filter == "hotel_reservation"
        assert config.deploy_from_source is True
        assert config.application_workspace is True
        assert config.spec_names == ["wrong_service_selector"]
        assert config.env.reuse_cluster is True
        assert config.env.force_recreate_cluster is True
        assert config.env.submit_done_returns_feedback is True

    def test_workspace_mode_is_preserved(self) -> None:
        defaults = {
            "agent": "cli_agent",
            "parallel": 1,
            "app_filter": "hotel_reservation",
            "deploy_from_source": True,
            "application_workspace": "ephemeral",
        }
        config = merge_stage_config(defaults, {})
        assert config.application_workspace == "ephemeral"

    def test_nested_override_preserves_siblings(self) -> None:
        """Override variants.count but keep variants.seed from defaults."""
        defaults = {
            "variants": {"seed": 99, "enabled": False, "count": 4},
        }
        overrides = {
            "variants": {"count": 16, "enabled": True},
        }
        config = merge_stage_config(defaults, overrides)
        assert config.variants.count == 16
        assert config.variants.enabled is True
        assert config.variants.seed == 99  # preserved from defaults

    def test_agent_config_override(self) -> None:
        defaults = {
            "agent_config": {"crucible": {"enable_judge": True, "kb_type": "structured"}},
        }
        overrides = {
            "agent_config": {"crucible": {"enable_judge": False}},
        }
        config = merge_stage_config(defaults, overrides)
        assert config.agent_config["crucible"]["enable_judge"] is False
        assert config.agent_config["crucible"]["kb_type"] == "structured"


# ---------------------------------------------------------------------------
# test_deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_flat(self) -> None:
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override(self) -> None:
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        over = {"x": {"b": 3, "c": 4}}
        assert _deep_merge(base, over) == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_does_not_mutate_base(self) -> None:
        base = {"x": {"a": 1}}
        _deep_merge(base, {"x": {"b": 2}})
        assert base == {"x": {"a": 1}}


# ---------------------------------------------------------------------------
# test_pipeline_state_roundtrip
# ---------------------------------------------------------------------------


class TestPipelineState:
    def test_roundtrip(self, tmp_path: Path) -> None:
        state = PipelineState(
            stages=[
                StageState(index=0, name="a", status="completed", experiment_dir="/tmp/a"),
                StageState(index=1, name="b", status="pending"),
            ]
        )
        write_pipeline_state(state, tmp_path)
        loaded = read_pipeline_state(tmp_path)
        assert len(loaded.stages) == 2
        assert loaded.stages[0].status == "completed"
        assert loaded.stages[0].experiment_dir == "/tmp/a"
        assert loaded.stages[1].status == "pending"

    def test_has_pipeline_state(self, tmp_path: Path) -> None:
        assert has_pipeline_state(tmp_path) is False
        state = PipelineState(stages=[StageState(index=0, name="a")])
        write_pipeline_state(state, tmp_path)
        assert has_pipeline_state(tmp_path) is True


# ---------------------------------------------------------------------------
# test_chain_kb
# ---------------------------------------------------------------------------


class TestChainKb:
    def test_chain_kb_sets_seed_dir(self) -> None:
        """chain_kb=true with a prior KB dir should set crucible_seed_kb_dir."""
        defaults = {"agent": "crucible"}
        config = merge_stage_config(defaults, {})
        # Simulate what the pipeline runner does
        prev_kb = "/some/experiment/kb"
        config.env.crucible_seed_kb_dir = prev_kb
        assert config.env.crucible_seed_kb_dir == prev_kb

    def test_chain_kb_false_no_seed(self) -> None:
        """chain_kb=false should leave crucible_seed_kb_dir empty."""
        defaults = {"agent": "crucible"}
        config = merge_stage_config(defaults, {})
        assert config.env.crucible_seed_kb_dir == ""


# ---------------------------------------------------------------------------
# test_pipeline_snapshot
# ---------------------------------------------------------------------------


class TestPipelineSnapshot:
    def test_write_and_read(self, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="snap-test",
            defaults={"agent": "crucible", "model": "gemini-flash"},
            stages=[
                StageConfig(name="s0", chain_kb=False, runner_overrides={"parallel": 8}),
                StageConfig(name="s1", chain_kb=True, chain_application_workspace=True),
            ],
        )
        write_pipeline_snapshot(config, tmp_path)
        from libs.sregym_lib.pipeline import read_pipeline_snapshot

        loaded = read_pipeline_snapshot(tmp_path)
        assert loaded.name == "snap-test"
        assert len(loaded.stages) == 2
        assert loaded.stages[0].name == "s0"
        assert loaded.stages[0].chain_kb is False
        assert loaded.stages[0].chain_application_workspace is False
        assert loaded.stages[1].chain_kb is True
        assert loaded.stages[1].chain_application_workspace is True


# ---------------------------------------------------------------------------
# test_reset_stages_for_rerun
# ---------------------------------------------------------------------------


class TestResetStagesForRerun:
    def _make_state_and_config(self, tmp_path: Path) -> tuple[PipelineConfig, PipelineState]:
        """Create a 4-stage pipeline: stages 0,1,2 chain, stage 3 independent."""
        config = PipelineConfig(
            name="rerun-test",
            stages=[
                StageConfig(name="s0", chain_kb=False),
                StageConfig(name="s1", chain_kb=True),
                StageConfig(name="s2", chain_kb=True, chain_application_workspace=True),
                StageConfig(name="s3", chain_kb=False),
            ],
        )

        # Create fake experiment dirs for all stages
        dirs = []
        for i, s in enumerate(config.stages):
            d = tmp_path / f"stage_{i}_{s.name}"
            d.mkdir()
            (d / "kb").mkdir()
            dirs.append(d)

        state = PipelineState(
            stages=[
                StageState(index=i, name=s.name, status="completed", experiment_dir=str(dirs[i]))
                for i, s in enumerate(config.stages)
            ]
        )

        return config, state

    def test_resets_chained_stages(self, tmp_path: Path) -> None:
        """Rerunning stage 1 should reset stages 1 and 2 (chained) but not 3."""
        config, state = self._make_state_and_config(tmp_path)
        reset_stages_for_rerun(config, state, from_stage=1, pipeline_dir=tmp_path)

        assert state.stages[0].status == "completed"
        assert state.stages[1].status == "pending"
        assert state.stages[2].status == "pending"
        assert state.stages[3].status == "completed"  # independent

    def test_renames_old_dir(self, tmp_path: Path) -> None:
        config, state = self._make_state_and_config(tmp_path)
        old_dir = Path(state.stages[1].experiment_dir)
        assert old_dir.exists()

        reset_stages_for_rerun(config, state, from_stage=1, pipeline_dir=tmp_path)

        assert not old_dir.exists()
        # Should have a timestamped backup
        backups = [p for p in tmp_path.iterdir() if p.name.startswith("stage_1_s1.")]
        assert len(backups) == 1

    def test_preserves_prior_kb(self, tmp_path: Path) -> None:
        """Rerunning stage 1 should not touch stage 0's experiment dir."""
        config, state = self._make_state_and_config(tmp_path)
        stage0_dir = Path(state.stages[0].experiment_dir)
        stage0_kb = stage0_dir / "kb"
        assert stage0_kb.exists()

        reset_stages_for_rerun(config, state, from_stage=1, pipeline_dir=tmp_path)

        assert stage0_dir.exists()
        assert stage0_kb.exists()
        assert state.stages[0].status == "completed"

    def test_invalid_stage_index(self, tmp_path: Path) -> None:
        config, state = self._make_state_and_config(tmp_path)
        with pytest.raises(ValueError, match="out of range"):
            reset_stages_for_rerun(config, state, from_stage=5, pipeline_dir=tmp_path)

    def test_extends_state_when_config_adds_new_stage(self, tmp_path: Path) -> None:
        config, state = self._make_state_and_config(tmp_path)
        state.stages = state.stages[:2]

        reset_stages_for_rerun(config, state, from_stage=2, pipeline_dir=tmp_path)

        assert len(state.stages) == 4
        assert state.stages[2].index == 2
        assert state.stages[2].name == "s2"
        assert state.stages[2].status == "pending"
        assert state.stages[2].experiment_dir == ""


# ---------------------------------------------------------------------------
# Pipeline runner tests (mocked subprocess)
# ---------------------------------------------------------------------------


from libs.sregym_lib import runner as runner_mod  # noqa: E402
from libs.sregym_lib.runner import StageHooks  # noqa: E402


class TestPipelineRunner:
    """Tests for ``libs.sregym_lib.runner.run_pipeline``."""

    @pytest.fixture
    def sregym_dir(self, tmp_path: Path):
        """Create a fake sregym dir with main.py."""
        d = tmp_path / "bench" / "sregym"
        d.mkdir(parents=True)
        (d / "main.py").write_text("# fake")
        return d

    def _make_config(self) -> PipelineConfig:
        return PipelineConfig(
            name="test",
            defaults={"agent": "crucible", "model": "gemini-flash"},
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(name="eval", chain_kb=True),
            ],
        )

    def test_runs_stages_sequentially(self, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        calls = []

        def mock_run(argv, cwd=None, env=None):
            calls.append(argv)
            return type("Result", (), {"returncode": 0})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch("subprocess.run", side_effect=mock_run):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 0
        assert len(calls) == 2

    def test_abort_on_failure(self, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        call_count = 0

        def mock_run(argv, cwd=None, env=None):
            nonlocal call_count
            call_count += 1
            return type("Result", (), {"returncode": 1})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch("subprocess.run", side_effect=mock_run):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 1
        assert call_count == 1  # stage 1 never ran

        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[0].status == "failed"
        assert loaded_state.stages[1].status == "pending"

    def test_resume_skips_completed(self, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        calls = []

        def mock_run(argv, cwd=None, env=None):
            calls.append(argv)
            return type("Result", (), {"returncode": 0})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        stage0_dir = pipeline_dir / "stage_0_build"
        stage0_dir.mkdir()
        (stage0_dir / "kb").mkdir()

        with patch("subprocess.run", side_effect=mock_run):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build", status="completed", experiment_dir=str(stage0_dir)),
                    StageState(index=1, name="eval", status="pending"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 0
        assert len(calls) == 1  # only stage 1 ran

    def test_kb_chaining_sets_seed_on_config(self, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        captured_configs = []

        def mock_run_stage(exp_config, stage_exp_dir, tasklist_path, sregym_dir, project_root, extra_env=None):
            captured_configs.append(exp_config)
            return 0

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", side_effect=mock_run_stage):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        # Stage 0 should not have crucible_seed_kb_dir set (chain_kb=false)
        assert captured_configs[0].env.crucible_seed_kb_dir == ""

        # Stage 1 should have crucible_seed_kb_dir pointing to stage 0's kb/
        seed_dir = captured_configs[1].env.crucible_seed_kb_dir
        assert seed_dir.endswith("/stage_0_build/kb")

    def test_hooks_invoked_for_kb_barrier(self, sregym_dir, tmp_path: Path) -> None:
        """before_stage + snapshot_before_drain + wait_for_drain fire around
        a stage whose successor chains its KB."""
        config = self._make_config()
        before_calls = []
        snapshot_calls = []
        wait_calls = []
        sentinel = object()

        def mock_run_stage(exp_config, stage_exp_dir, tasklist_path, sregym_dir, project_root, extra_env=None):
            return 0

        def before_stage(exp_dir, cfg):
            before_calls.append(exp_dir)

        def snap(exp_dir, cfg):
            snapshot_calls.append(exp_dir)
            return sentinel

        def wait(exp_dir, baseline):
            wait_calls.append((exp_dir, baseline))

        hooks = StageHooks(
            before_stage=before_stage,
            snapshot_before_drain=snap,
            wait_for_drain=wait,
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", side_effect=mock_run_stage):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
                hooks=hooks,
            )

        assert rc == 0
        # before_stage fires for both stages
        assert before_calls == [
            pipeline_dir / "stage_0_build",
            pipeline_dir / "stage_1_eval",
        ]
        # snapshot + wait fire only around stage 0 (stage 1 chains from it)
        assert snapshot_calls == [pipeline_dir / "stage_0_build"]
        assert wait_calls == [(pipeline_dir / "stage_0_build", sentinel)]

    def test_abort_on_kb_queue_drain_failure(self, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()

        def wait_fails(exp_dir, baseline):
            raise TimeoutError("queue stuck")

        hooks = StageHooks(
            snapshot_before_drain=lambda exp_dir, cfg: object(),
            wait_for_drain=wait_fails,
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", return_value=0):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
                hooks=hooks,
            )

        assert rc == 1
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[0].status == "failed"
        assert loaded_state.stages[0].error == "kb queue drain failed: queue stuck"
        assert loaded_state.stages[1].status == "pending"

    def test_chain_application_workspace_sets_seed_env(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={
                "agent": "cli_agent",
                "model": "claude-sonnet-4-5",
                "parallel": 1,
                "app_filter": "hotel_reservation",
                "deploy_from_source": True,
                "application_workspace": True,
            },
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(name="eval", chain_kb=False, chain_application_workspace=True),
            ],
        )
        captured_envs = []

        def mock_run_stage(exp_config, stage_exp_dir, tasklist_path, sregym_dir, project_root, extra_env=None):
            captured_envs.append(extra_env or {})
            if stage_exp_dir.name == "stage_0_build":
                workspace = stage_exp_dir / "application_workspace"
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / "README.md").write_text("seed\n", encoding="utf-8")
            return 0

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", side_effect=mock_run_stage):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 0
        assert captured_envs[0] == {}
        assert captured_envs[1]["SREGYM_APP_WORKSPACE_SEED_DIR"].endswith("/stage_0_build/application_workspace")

    def test_chain_application_workspace_missing_source_fails(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={
                "agent": "cli_agent",
                "model": "claude-sonnet-4-5",
                "parallel": 1,
                "app_filter": "hotel_reservation",
                "deploy_from_source": True,
                "application_workspace": True,
            },
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(name="eval", chain_kb=False, chain_application_workspace=True),
            ],
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", return_value=0):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 1
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[1].status == "failed"
        assert "application workspace is missing" in loaded_state.stages[1].error

    def test_chain_application_workspace_requires_workspace_mode(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={
                "agent": "cli_agent",
                "model": "claude-sonnet-4-5",
                "parallel": 1,
                "app_filter": "hotel_reservation",
                "deploy_from_source": True,
                "application_workspace": True,
            },
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(
                    name="eval",
                    chain_kb=False,
                    chain_application_workspace=True,
                    runner_overrides={"application_workspace": False},
                ),
            ],
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", return_value=0):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 1
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[1].status == "failed"
        assert "requires application_workspace = 'persistent'" in loaded_state.stages[1].error

    def test_chain_application_workspace_requires_persistent_workspace(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={
                "agent": "cli_agent",
                "model": "claude-sonnet-4-5",
                "parallel": 1,
                "app_filter": "hotel_reservation",
                "deploy_from_source": True,
                "application_workspace": "persistent",
            },
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(
                    name="eval",
                    chain_kb=False,
                    chain_application_workspace=True,
                    runner_overrides={"application_workspace": "ephemeral"},
                ),
            ],
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", return_value=0):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 1
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[1].status == "failed"
        assert "requires application_workspace = 'persistent'" in loaded_state.stages[1].error

    def test_chain_application_workspace_requires_matching_app_filter(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={
                "agent": "cli_agent",
                "model": "claude-sonnet-4-5",
                "parallel": 1,
                "app_filter": "hotel_reservation",
                "deploy_from_source": True,
                "application_workspace": True,
            },
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(
                    name="eval",
                    chain_kb=False,
                    chain_application_workspace=True,
                    runner_overrides={"app_filter": "social_network"},
                ),
            ],
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        def mock_run_stage(exp_config, stage_exp_dir, tasklist_path, sregym_dir, project_root, extra_env=None):
            workspace = stage_exp_dir / "application_workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            return 0

        with patch.object(runner_mod, "_run_stage", side_effect=mock_run_stage):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 1
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[1].status == "failed"
        assert "matching app_filter values" in loaded_state.stages[1].error

    def test_resume_extends_state_for_new_stage(self, sregym_dir, tmp_path: Path) -> None:
        config = PipelineConfig(
            name="test",
            defaults={"agent": "crucible", "model": "gemini-flash"},
            stages=[
                StageConfig(name="build", chain_kb=False),
                StageConfig(name="eval", chain_kb=True),
                StageConfig(name="eval_again", chain_kb=False),
            ],
        )

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with patch.object(runner_mod, "_run_stage", return_value=0):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner_mod.run_pipeline(
                config,
                project_root=tmp_path,
                sregym_dir=sregym_dir,
                pipeline_dir=pipeline_dir,
                state=state,
            )

        assert rc == 0
        loaded_state = read_pipeline_state(pipeline_dir)
        assert [stage.name for stage in loaded_state.stages] == ["build", "eval", "eval_again"]
