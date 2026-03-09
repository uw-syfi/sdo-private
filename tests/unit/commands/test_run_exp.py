import argparse
import json
from pathlib import Path
from unittest.mock import patch

try:
    import tomllib
except ImportError:
    import tomli as tomllib

import pytest

from app_operator.commands.run_exp import (
    AppResult,
    AppStatus,
    _extract_results,
    _resolve_experiment,
    _write_experiment_sds_config,
    _write_toml_simple,
    add_arguments,
    run_command,
)
from app_operator.config import load_config


class TestWriteTomlSimple:
    def test_bool_values(self):
        result = _write_toml_simple({"enabled": True, "disabled": False})
        assert "enabled = true" in result
        assert "disabled = false" in result

    def test_int_values(self):
        result = _write_toml_simple({"count": 42})
        assert "count = 42" in result

    def test_str_values(self):
        result = _write_toml_simple({"name": "hello"})
        assert 'name = "hello"' in result

    def test_list_values(self):
        result = _write_toml_simple({"items": ["a", "b"]})
        assert 'items = ["a", "b"]' in result

    def test_nested_table(self):
        data = {"section": {"key": "value"}}
        result = _write_toml_simple(data)
        assert "[section]" in result
        assert 'key = "value"' in result

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported TOML value type"):
            _write_toml_simple({"bad": 3.14})


class TestWriteSdsConfig:
    def test_write_sds_config_basic(self, tmp_path):
        """Writes correct TOML for [agent] + [operator] sections."""
        config = {
            "apps": ["app1", "app2"],
            "agent": {"provider": "gemini", "model": "gemini-2.0-flash"},
            "operator": {"monitoring_max_iters": 3},
        }
        exp_dir = tmp_path / "exp" / "myapp" / "test_exp"
        exp_dir.mkdir(parents=True)

        _write_experiment_sds_config(exp_dir, config)

        sds_toml = exp_dir / "sds.toml"
        assert sds_toml.exists()

        with open(sds_toml, "rb") as f:
            parsed = tomllib.load(f)

        assert parsed["agent"]["provider"] == "gemini"
        assert parsed["agent"]["model"] == "gemini-2.0-flash"
        assert parsed["operator"]["monitoring_max_iters"] == 3
        assert "apps" not in parsed

    def test_write_sds_config_apps_only(self, tmp_path):
        """No-op when config only has apps."""
        config = {"apps": ["app1"]}
        exp_dir = tmp_path / "exp" / "myapp" / "test_exp"
        exp_dir.mkdir(parents=True)

        _write_experiment_sds_config(exp_dir, config)

        sds_toml = exp_dir / "sds.toml"
        assert not sds_toml.exists()

    def test_write_sds_config_nested(self, tmp_path):
        """Handles [operator.phase] correctly."""
        config = {
            "apps": ["app1"],
            "operator": {
                "monitoring_max_iters": 5,
                "phase": {"fix_summary_consolidation": True, "code_analysis": False},
            },
        }
        exp_dir = tmp_path / "exp" / "myapp" / "test_exp"
        exp_dir.mkdir(parents=True)

        _write_experiment_sds_config(exp_dir, config)

        sds_toml = exp_dir / "sds.toml"
        with open(sds_toml, "rb") as f:
            parsed = tomllib.load(f)

        assert parsed["operator"]["monitoring_max_iters"] == 5
        assert parsed["operator"]["phase"]["fix_summary_consolidation"] is True
        assert parsed["operator"]["phase"]["code_analysis"] is False

    def test_write_sds_config_roundtrip(self, tmp_path):
        """Written file is loadable by load_config() and produces correct Config."""
        config = {
            "apps": ["app1"],
            "agent": {"provider": "gemini", "model": "test-model"},
            "operator": {
                "monitoring_max_iters": 3,
                "phase": {"fix_summary_consolidation": False},
            },
        }
        exp_dir = tmp_path / "exp" / "myapp" / "test_exp"
        exp_dir.mkdir(parents=True)

        _write_experiment_sds_config(exp_dir, config)

        loaded = load_config(str(exp_dir))
        assert loaded.agent.provider == "gemini"
        assert loaded.operator.monitoring_max_iters == 3
        assert loaded.operator.phase.fix_summary_consolidation is False

    def test_write_sds_config_overwrites_existing(self, tmp_path):
        """If app had its own sds.toml, experiment config replaces it."""
        exp_dir = tmp_path / "exp" / "myapp" / "test_exp"
        exp_dir.mkdir(parents=True)

        # Write an existing sds.toml
        existing = exp_dir / "sds.toml"
        existing.write_text('[agent]\nprovider = "codex"\n')

        # Overwrite with experiment config
        config = {
            "apps": ["app1"],
            "agent": {"provider": "gemini"},
        }
        _write_experiment_sds_config(exp_dir, config)

        with open(existing, "rb") as f:
            parsed = tomllib.load(f)

        assert parsed["agent"]["provider"] == "gemini"
        # Old content should be gone
        assert "codex" not in existing.read_text()


class TestExtractResults:
    def test_extract_from_trajectory(self, tmp_path):
        """Parses a minimal trajectory JSON and returns correct iterations + status."""
        traj_dir = tmp_path / ".sds" / "trajectories"
        traj_dir.mkdir(parents=True)
        traj = {
            "metadata": {"status": "completed"},
            "deployment": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
        }
        (traj_dir / "trajectory_001.json").write_text(json.dumps(traj))

        result = _extract_results(tmp_path)

        assert result["status"] == "completed"
        assert result["deployment_iterations"] == 3

    def test_no_trajectory_dir(self, tmp_path):
        """Returns unknown/None when no .sds/trajectories exists."""
        result = _extract_results(tmp_path)

        assert result["status"] == "unknown"
        assert result["deployment_iterations"] is None

    def test_empty_deployment_phase(self, tmp_path):
        """Returns 0 iterations when deployment array is empty."""
        traj_dir = tmp_path / ".sds" / "trajectories"
        traj_dir.mkdir(parents=True)
        traj = {
            "metadata": {"status": "failed"},
            "deployment": [],
        }
        (traj_dir / "trajectory_001.json").write_text(json.dumps(traj))

        result = _extract_results(tmp_path)

        assert result["status"] == "failed"
        assert result["deployment_iterations"] == 0

    def test_multiple_trajectories_uses_latest(self, tmp_path):
        """Picks the last trajectory file alphabetically."""
        traj_dir = tmp_path / ".sds" / "trajectories"
        traj_dir.mkdir(parents=True)

        old_traj = {
            "metadata": {"status": "failed"},
            "deployment": [{"attempt": 1}],
        }
        (traj_dir / "trajectory_001.json").write_text(json.dumps(old_traj))

        new_traj = {
            "metadata": {"status": "completed"},
            "deployment": [{"attempt": 1}, {"attempt": 2}],
        }
        (traj_dir / "trajectory_002.json").write_text(json.dumps(new_traj))

        result = _extract_results(tmp_path)

        assert result["status"] == "completed"
        assert result["deployment_iterations"] == 2


class TestAddArguments:
    def test_single_experiment(self):
        """Single experiment is stored as a one-element list."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["my-exp"])
        assert args.experiments == ["my-exp"]

    def test_multiple_experiments(self):
        """Multiple experiments are stored as a list."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a", "exp-b", "exp-c"])
        assert args.experiments == ["exp-a", "exp-b", "exp-c"]

    def test_no_experiments_fails(self):
        """Omitting experiments argument raises SystemExit (required)."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_parallel_default(self):
        """--parallel defaults to 1."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a"])
        assert args.parallel == 1

    def test_parallel_custom(self):
        """--parallel accepts a custom value."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a", "--parallel", "4"])
        assert args.parallel == 4

    def test_rerun_default_is_none(self):
        """--rerun defaults to None."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a"])
        assert args.rerun is None

    def test_rerun_failed(self):
        """--rerun failed sets rerun to 'failed'."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a", "--rerun", "failed"])
        assert args.rerun == "failed"

    def test_rerun_all(self):
        """--rerun all sets rerun to 'all'."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(["exp-a", "--rerun", "all"])
        assert args.rerun == "all"


class TestResolveExperiment:
    def test_resolve_by_name(self, tmp_path, monkeypatch):
        """Resolves experiment by name from exp_config/<name>/config.toml."""
        monkeypatch.chdir(tmp_path)
        exp_dir = tmp_path / "exp_config" / "my-exp"
        exp_dir.mkdir(parents=True)
        (exp_dir / "config.toml").write_bytes(b'apps = ["/some/app"]\n')

        result = _resolve_experiment("my-exp")

        assert result is not None
        exp_name, config_path, config, log_dir = result
        assert exp_name == "my-exp"
        assert config["apps"] == ["/some/app"]
        # log_dir may be relative; compare resolved paths
        assert log_dir.resolve() == (exp_dir / "logs").resolve()

    def test_resolve_by_direct_path(self, tmp_path):
        """Resolves experiment from a direct path to a TOML file.

        exp_name is derived from the parent directory name (not the file stem)
        when the path is outside the exp_config structure.
        """
        config_dir = tmp_path / "my-exp"
        config_dir.mkdir()
        config_file = config_dir / "config.toml"
        config_file.write_bytes(b'apps = ["/some/app"]\n')

        result = _resolve_experiment(str(config_file))

        assert result is not None
        exp_name, config_path, config, log_dir = result
        assert exp_name == "my-exp"
        assert config_path == config_file
        assert config["apps"] == ["/some/app"]

    def test_resolve_missing_returns_none(self, tmp_path, monkeypatch):
        """Returns None when neither exp_config nor direct path resolves."""
        monkeypatch.chdir(tmp_path)

        result = _resolve_experiment("nonexistent-exp")

        assert result is None


class TestRunCommandMultiExperiment:
    """Tests for run_command with multiple experiments."""

    def _make_config(self, path: Path, apps: list[str]) -> None:
        """Write a minimal experiment config.toml."""
        lines = ["apps = [\n"]
        for app in apps:
            escaped = app.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'  "{escaped}",\n')
        lines.append("]\n")
        path.write_text("".join(lines))

    def test_run_command_single_experiment_backward_compat(self, tmp_path, monkeypatch):
        """Single experiment in args.experiments behaves like before."""
        monkeypatch.chdir(tmp_path)

        exp_dir = tmp_path / "exp_config" / "exp-a"
        exp_dir.mkdir(parents=True)
        self._make_config(exp_dir / "config.toml", ["/app/hotel"])

        args = argparse.Namespace(experiments=["exp-a"], parallel=1, rerun=None)

        fake_result = AppResult(app="hotel", status=AppStatus.COMPLETED, deployment_iterations=2)

        with patch("app_operator.commands.run_exp.run_experiment_task", return_value=fake_result):
            rc = run_command(args)

        assert rc == 0
        results_file = exp_dir / "logs" / "results.json"
        assert results_file.exists()
        data = json.loads(results_file.read_text())
        assert data["experiment"] == "exp-a"

    def test_run_command_multi_experiment_both_processed(self, tmp_path, monkeypatch):
        """Both experiments are processed and each gets its own results.json."""
        monkeypatch.chdir(tmp_path)

        for name in ("exp-a", "exp-b"):
            d = tmp_path / "exp_config" / name
            d.mkdir(parents=True)
            self._make_config(d / "config.toml", [f"/app/{name}-svc"])

        args = argparse.Namespace(experiments=["exp-a", "exp-b"], parallel=2, rerun=None)

        def fake_task(app_path_str, exp_name, progress, task_id, log_dir, *a, **kw):
            progress.start_task(task_id)
            return AppResult(app=Path(app_path_str).name, status=AppStatus.COMPLETED, deployment_iterations=1)

        with patch("app_operator.commands.run_exp.run_experiment_task", side_effect=fake_task):
            rc = run_command(args)

        assert rc == 0
        for name in ("exp-a", "exp-b"):
            results_file = tmp_path / "exp_config" / name / "logs" / "results.json"
            assert results_file.exists(), f"Missing results.json for {name}"
            data = json.loads(results_file.read_text())
            assert data["experiment"] == name

    def test_run_command_missing_experiment_returns_1(self, tmp_path, monkeypatch):
        """Returns 1 immediately if any experiment config cannot be resolved."""
        monkeypatch.chdir(tmp_path)

        # exp-a exists, exp-missing does not
        d = tmp_path / "exp_config" / "exp-a"
        d.mkdir(parents=True)
        self._make_config(d / "config.toml", ["/app/svc"])

        args = argparse.Namespace(experiments=["exp-a", "exp-missing"], parallel=1, rerun=None)

        rc = run_command(args)

        assert rc == 1

    def test_run_command_invalid_repeats_returns_1(self, tmp_path, monkeypatch):
        """Returns 1 when repeats is not a positive integer."""
        monkeypatch.chdir(tmp_path)

        d = tmp_path / "exp_config" / "exp-bad"
        d.mkdir(parents=True)
        (d / "config.toml").write_text('apps = ["/app/svc"]\nrepeats = 0\n')

        args = argparse.Namespace(experiments=["exp-bad"], parallel=1, rerun=None)

        rc = run_command(args)

        assert rc == 1

    def _write_results_json(self, log_dir: Path, exp_name: str, results: list[dict]) -> None:
        """Write a minimal results.json to simulate a previous run."""
        log_dir.mkdir(parents=True, exist_ok=True)
        data = {"experiment": exp_name, "results": results}
        (log_dir / "results.json").write_text(json.dumps(data))

    def test_rerun_all_ignores_existing_results(self, tmp_path, monkeypatch):
        """--rerun all reruns apps even if they already have results."""
        monkeypatch.chdir(tmp_path)

        exp_dir = tmp_path / "exp_config" / "exp-a"
        exp_dir.mkdir(parents=True)
        self._make_config(exp_dir / "config.toml", ["/app/hotel"])

        # Pre-populate results.json with a successful run
        self._write_results_json(
            exp_dir / "logs",
            "exp-a",
            [{"app": "hotel", "status": "completed", "deployment_iterations": 1}],
        )

        fake_result = AppResult(app="hotel", status=AppStatus.COMPLETED, deployment_iterations=1)
        args = argparse.Namespace(experiments=["exp-a"], parallel=1, rerun="all")

        with patch("app_operator.commands.run_exp.run_experiment_task", return_value=fake_result) as mock_task:
            rc = run_command(args)

        assert rc == 0
        called_apps = [call.args[0] for call in mock_task.call_args_list]
        assert any("hotel" in a for a in called_apps), "Expected hotel to be rerun with --rerun all"

    def test_rerun_failed_reruns_failed_skips_successful(self, tmp_path, monkeypatch):
        """--rerun failed reruns only failed apps, skips successful ones."""
        monkeypatch.chdir(tmp_path)

        exp_dir = tmp_path / "exp_config" / "exp-a"
        exp_dir.mkdir(parents=True)
        self._make_config(exp_dir / "config.toml", ["/app/hotel", "/app/social"])

        # Pre-populate results: hotel=success, social=failed
        self._write_results_json(
            exp_dir / "logs",
            "exp-a",
            [
                {"app": "hotel", "status": "completed", "deployment_iterations": 1},
                {"app": "social", "status": "failed", "deployment_iterations": 2},
            ],
        )

        fake_result = AppResult(app="social", status=AppStatus.COMPLETED, deployment_iterations=1)
        args = argparse.Namespace(experiments=["exp-a"], parallel=1, rerun="failed")

        with patch("app_operator.commands.run_exp.run_experiment_task", return_value=fake_result) as mock_task:
            rc = run_command(args)

        assert rc == 0
        called_apps = [call.args[0] for call in mock_task.call_args_list]
        assert not any("hotel" in a for a in called_apps), "hotel (successful) should be skipped"
        assert any("social" in a for a in called_apps), "social (failed) should be rerun"

    def test_rerun_failed_reruns_when_status_failed(self, tmp_path, monkeypatch):
        """--rerun failed reruns a single failed app (status='failed').

        Also covers backward compat: old results.json files may have had a
        'success' key that disagreed with 'status'. Now only 'status' is
        authoritative so even legacy files with success=True + status=failed
        are treated as failed.
        """
        monkeypatch.chdir(tmp_path)

        exp_dir = tmp_path / "exp_config" / "exp-a"
        exp_dir.mkdir(parents=True)
        self._make_config(exp_dir / "config.toml", ["/app/hotel"])

        self._write_results_json(
            exp_dir / "logs",
            "exp-a",
            [{"app": "hotel", "status": "failed", "deployment_iterations": 15}],
        )

        fake_result = AppResult(app="hotel", status=AppStatus.COMPLETED, deployment_iterations=1)
        args = argparse.Namespace(experiments=["exp-a"], parallel=1, rerun="failed")

        with patch("app_operator.commands.run_exp.run_experiment_task", return_value=fake_result) as mock_task:
            rc = run_command(args)

        assert rc == 0
        called_apps = [call.args[0] for call in mock_task.call_args_list]
        assert any("hotel" in a for a in called_apps), "hotel (status=failed) should be rerun"

    def test_no_rerun_skips_all_existing(self, tmp_path, monkeypatch):
        """Default behavior (no --rerun) skips all apps already in results.json."""
        monkeypatch.chdir(tmp_path)

        exp_dir = tmp_path / "exp_config" / "exp-a"
        exp_dir.mkdir(parents=True)
        self._make_config(exp_dir / "config.toml", ["/app/hotel"])

        # Pre-populate with a completed result
        self._write_results_json(
            exp_dir / "logs",
            "exp-a",
            [{"app": "hotel", "status": "completed", "deployment_iterations": 1}],
        )

        args = argparse.Namespace(experiments=["exp-a"], parallel=1, rerun=None)

        with patch("app_operator.commands.run_exp.run_experiment_task") as mock_task:
            rc = run_command(args)

        assert rc == 0
        assert mock_task.call_count == 0, "hotel should be skipped without --rerun"
