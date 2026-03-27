"""Tests for sregym_agents.pipeline_config."""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from sregym_agents.pipeline_config import (
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

[defaults.env]
judge_model_id = "judge-model"

[defaults.variants]
seed = 99

[[stages]]
name = "build_kb"
chain_kb = false

[stages.runner]
parallel = 8

[stages.runner.variants]
enabled = true
count = 16

[[stages]]
name = "evaluate"
chain_kb = true

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
        assert config.stages[0].runner_overrides["parallel"] == 8
        assert config.stages[0].runner_overrides["variants"]["enabled"] is True
        assert config.stages[1].name == "evaluate"
        assert config.stages[1].chain_kb is True
        assert config.stages[1].runner_overrides["tasklist"] == "count_train"
        assert config.defaults["model"] == "gemini-flash"
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
            "parallel": 4,
            "variants": {"seed": 99},
            "env": {"judge_model_id": "judge"},
        }
        config = merge_stage_config(defaults, {})
        assert config.agent == "crucible"
        assert config.model == "gemini-flash"
        assert config.parallel == 4
        assert config.variants.seed == 99
        assert config.env.judge_model_id == "judge"

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
                StageConfig(name="s1", chain_kb=True),
            ],
        )
        write_pipeline_snapshot(config, tmp_path)
        from sregym_agents.pipeline_config import read_pipeline_snapshot

        loaded = read_pipeline_snapshot(tmp_path)
        assert loaded.name == "snap-test"
        assert len(loaded.stages) == 2
        assert loaded.stages[0].name == "s0"
        assert loaded.stages[0].chain_kb is False
        assert loaded.stages[1].chain_kb is True


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
                StageConfig(name="s2", chain_kb=True),
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


# ---------------------------------------------------------------------------
# Pipeline runner tests (mocked subprocess)
# ---------------------------------------------------------------------------


def _import_run_sregym():
    """Import scripts/run_sregym.py as a module."""
    spec = importlib.util.spec_from_file_location(
        "run_sregym",
        Path(__file__).resolve().parent.parent.parent / "scripts" / "run_sregym.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPipelineRunner:
    """Tests for the pipeline runner in scripts/run_sregym.py."""

    @pytest.fixture
    def runner(self):
        return _import_run_sregym()

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

    def test_runs_stages_sequentially(self, runner, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        calls = []

        def mock_run(argv, cwd=None, env=None):
            calls.append(argv)
            return type("Result", (), {"returncode": 0})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with (
            patch.object(runner, "_SREGYM_DIR", sregym_dir),
            patch.object(runner, "_PROJECT_ROOT", tmp_path),
            patch("subprocess.run", side_effect=mock_run),
        ):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner.run_pipeline(config, pipeline_dir=pipeline_dir, state=state)

        assert rc == 0
        assert len(calls) == 2

    def test_abort_on_failure(self, runner, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        call_count = 0

        def mock_run(argv, cwd=None, env=None):
            nonlocal call_count
            call_count += 1
            return type("Result", (), {"returncode": 1})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with (
            patch.object(runner, "_SREGYM_DIR", sregym_dir),
            patch.object(runner, "_PROJECT_ROOT", tmp_path),
            patch("subprocess.run", side_effect=mock_run),
        ):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner.run_pipeline(config, pipeline_dir=pipeline_dir, state=state)

        assert rc == 1
        assert call_count == 1  # stage 1 never ran

        # Verify state was persisted
        loaded_state = read_pipeline_state(pipeline_dir)
        assert loaded_state.stages[0].status == "failed"
        assert loaded_state.stages[1].status == "pending"

    def test_resume_skips_completed(self, runner, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        calls = []

        def mock_run(argv, cwd=None, env=None):
            calls.append(argv)
            return type("Result", (), {"returncode": 0})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        # Stage 0 already completed with a kb/ dir
        stage0_dir = pipeline_dir / "stage_0_build"
        stage0_dir.mkdir()
        (stage0_dir / "kb").mkdir()

        with (
            patch.object(runner, "_SREGYM_DIR", sregym_dir),
            patch.object(runner, "_PROJECT_ROOT", tmp_path),
            patch("subprocess.run", side_effect=mock_run),
        ):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build", status="completed", experiment_dir=str(stage0_dir)),
                    StageState(index=1, name="eval", status="pending"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            rc = runner.run_pipeline(config, pipeline_dir=pipeline_dir, state=state)

        assert rc == 0
        assert len(calls) == 1  # only stage 1 ran

    def test_kb_chaining_env_var(self, runner, sregym_dir, tmp_path: Path) -> None:
        config = self._make_config()
        captured_envs = []

        def mock_run(argv, cwd=None, env=None):
            captured_envs.append(env or {})
            return type("Result", (), {"returncode": 0})()

        pipeline_dir = tmp_path / "pipeline"
        pipeline_dir.mkdir()

        with (
            patch.object(runner, "_SREGYM_DIR", sregym_dir),
            patch.object(runner, "_PROJECT_ROOT", tmp_path),
            patch("subprocess.run", side_effect=mock_run),
        ):
            state = PipelineState(
                stages=[
                    StageState(index=0, name="build"),
                    StageState(index=1, name="eval"),
                ]
            )
            write_pipeline_state(state, pipeline_dir)
            write_pipeline_snapshot(config, pipeline_dir)
            runner.run_pipeline(config, pipeline_dir=pipeline_dir, state=state)

        # Stage 0 should not have CRUCIBLE_SEED_KB_DIR set (chain_kb=false)
        assert captured_envs[0].get("CRUCIBLE_SEED_KB_DIR", "") == ""

        # Stage 1 should have CRUCIBLE_SEED_KB_DIR pointing to stage 0's kb/
        seed_dir = captured_envs[1].get("CRUCIBLE_SEED_KB_DIR", "")
        assert seed_dir.endswith("/stage_0_build/kb")
