import json

try:
    import tomllib
except ImportError:
    import tomli as tomllib

import pytest

from app_operator.commands.run_exp import (
    _extract_results,
    _write_experiment_sds_config,
    _write_toml_simple,
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
            "agent": {"provider": "gemini"},
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
