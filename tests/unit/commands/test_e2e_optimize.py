"""Unit tests for app_operator.commands.e2e_optimize."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app_operator.commands.e2e_optimize import (
    _cleanup_disk_between_iterations,
    _init_experiment,
    run_command,
)


def _write_e2e_config(
    config_path: Path,
    train_apps: list[Path],
    val_apps: list[Path] | None = None,
    iterations: int = 1,
    extra_lines: list[str] | None = None,
) -> None:
    """Write a minimal e2e config TOML for tests."""
    val_apps = val_apps or []
    extra_lines = extra_lines or []
    train_items = ", ".join(f'"{p}"' for p in train_apps)
    val_items = ", ".join(f'"{p}"' for p in val_apps)
    config_path.write_text(
        "\n".join(
            [
                f"iterations = {iterations}",
                'prompts = ["deployer_fix_error"]',
                *extra_lines,
                "[training]",
                f"apps = [{train_items}]",
                "[validation]",
                f"apps = [{val_items}]",
                "",
            ]
        )
    )


def _fake_app_config(
    *,
    provider: str = "gemini",
    model: str | None = "gemini-2.5-pro",
    location: str | None = "us-central1",
    n_candidates: int = 1,
    selection_mode: str = "hybrid",
    selection_top_k: int = 3,
):
    """Build a lightweight app config object for run_command tests."""
    return SimpleNamespace(
        agent=SimpleNamespace(backend=provider, model=model, location=location),
        dspy=SimpleNamespace(
            optimization=SimpleNamespace(
                teacher_model="gemini-2.5-pro",
                n_candidates=n_candidates,
                selection_mode=selection_mode,
                selection_top_k=selection_top_k,
            )
        ),
    )


def test_run_command_dry_run_validates_without_execution(tmp_path):
    """Dry run should validate inputs without running optimization or validation."""
    train_app = tmp_path / "train-app"
    val_app = tmp_path / "val-app"
    train_app.mkdir()
    val_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(cfg, [train_app], [val_app])

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=True,
        work_dir=str(tmp_path / "work"),
    )

    with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
        with patch("app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling") as mock_run:
            rc = run_command(args)

    assert rc == 0
    assert not (tmp_path / "work").exists()
    mock_eval.assert_not_called()
    mock_run.assert_not_called()


def test_run_command_dry_run_fails_when_training_app_missing(tmp_path):
    """Dry run should fail fast if training app paths are invalid."""
    missing_train = tmp_path / "missing-train-app"
    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(cfg, [missing_train], [])

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=True,
        work_dir=str(tmp_path / "work"),
    )

    with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
        with patch("app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling") as mock_run:
            rc = run_command(args)

    assert rc == 1
    mock_eval.assert_not_called()
    mock_run.assert_not_called()


def test_run_command_dry_run_fails_for_non_int_iterations(tmp_path):
    """Dry run should return a clean error when iterations has wrong TOML type."""
    train_app = tmp_path / "train-app"
    train_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    cfg.write_text(
        "\n".join(
            [
                'iterations = "2"',
                'prompts = ["deployer_fix_error"]',
                "[training]",
                f'apps = ["{train_app}"]',
                "",
            ]
        )
    )

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=True,
        work_dir=str(tmp_path / "work"),
    )

    with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
        with patch("app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling") as mock_run:
            rc = run_command(args)

    assert rc == 1
    mock_eval.assert_not_called()
    mock_run.assert_not_called()


def test_run_command_dry_run_fails_for_empty_training_apps(tmp_path):
    """Dry run should reject empty [training].apps before optimization starts."""
    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(cfg, [], [])

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=True,
        work_dir=str(tmp_path / "work"),
    )

    with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
        with patch("app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling") as mock_run:
            rc = run_command(args)

    assert rc == 1
    mock_eval.assert_not_called()
    mock_run.assert_not_called()


def test_run_command_continues_when_validation_fails(tmp_path):
    """Validation subprocess failures should not stop later iterations."""
    train_app = tmp_path / "train-app"
    val_app = tmp_path / "val-app"
    train_app.mkdir()
    val_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(cfg, [train_app], [val_app], iterations=2)

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=False,
        work_dir=str(tmp_path / "work"),
    )

    exp_path = tmp_path / "work" / "val-exp"
    exp_path.mkdir(parents=True, exist_ok=True)
    app_config = _fake_app_config()

    with patch(
        "app_operator.commands.e2e_optimize.load_app_config",
        return_value=app_config,
    ):
        with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
            mock_eval.return_value.optimize.return_value = {"success": True}
            with patch(
                "app_operator.commands.e2e_optimize._init_experiment",
                return_value=exp_path,
            ):
                with patch("app_operator.commands.e2e_optimize._update_sds_toml"):
                    with patch(
                        "app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling",
                        return_value=(None, False, "validation failed"),
                    ):
                        rc = run_command(args)
        assert mock_eval.return_value.optimize.call_count == 2

    assert rc == 0
    state = json.loads((tmp_path / "work" / "state.json").read_text())
    assert state["completed_val_apps"] == []
    assert state["current_iteration"] == 3


def test_run_command_infers_provider_from_app_model_when_no_override(tmp_path):
    """When config model override is absent, provider should be inferred from app model."""
    train_app = tmp_path / "train-app"
    train_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(cfg, [train_app], [])

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=False,
        work_dir=str(tmp_path / "work"),
    )

    app_config = _fake_app_config(provider="rlm", model="openai/gpt-4o-mini")
    captured_provider: dict[str, str] = {}

    with patch(
        "app_operator.commands.e2e_optimize.load_app_config",
        return_value=app_config,
    ):
        with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:

            def _optimize_side_effect(**kwargs):
                captured_provider["provider"] = kwargs["provider"]
                return {"success": True}

            mock_eval.return_value.optimize.side_effect = _optimize_side_effect
            rc = run_command(args)

    assert rc == 0
    assert captured_provider["provider"] == "openai"


def test_run_command_applies_dspy_optimization_overrides(tmp_path):
    """Per-experiment [dspy_optimization] should override selection mode and candidates."""
    train_app = tmp_path / "train-app"
    train_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(
        cfg,
        [train_app],
        [],
        extra_lines=[
            "[dspy_optimization]",
            'selection_mode = "llm"',
            "n_candidates = 2",
        ],
    )

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=False,
        work_dir=str(tmp_path / "work"),
    )
    app_config = _fake_app_config(n_candidates=1, selection_mode="hybrid")

    with patch(
        "app_operator.commands.e2e_optimize.load_app_config",
        return_value=app_config,
    ):
        with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
            mock_eval.return_value.optimize.return_value = {"success": True}
            rc = run_command(args)

    assert rc == 0
    assert mock_eval.call_args.kwargs["n_candidates"] == 2
    effective_config = mock_eval.call_args.kwargs["config"]
    assert effective_config.optimization.selection_mode == "llm"


def test_run_command_cleans_up_compose_projects_on_interrupt(tmp_path):
    """Keyboard interrupts should tear down active Compose projects in the workdir."""
    train_app = tmp_path / "train-app"
    train_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    work_dir = tmp_path / "work"
    _write_e2e_config(cfg, [train_app], [])

    exp_dir = work_dir / "iter1_c1_train-app"
    exp_dir.mkdir(parents=True)
    (exp_dir / "docker-compose.yml").write_text("services: {}\n")

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=False,
        work_dir=str(work_dir),
    )
    app_config = _fake_app_config()

    with patch(
        "app_operator.commands.e2e_optimize.load_app_config",
        return_value=app_config,
    ):
        with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
            mock_eval.return_value.optimize.side_effect = KeyboardInterrupt()
            with patch("app_operator.commands.e2e_optimize.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
                rc = run_command(args)

    assert rc == 130
    assert any(
        call.args[0]
        == [
            "docker",
            "compose",
            "-f",
            str(exp_dir / "docker-compose.yml"),
            "--project-name",
            exp_dir.name,
            "down",
            "--volumes",
            "--remove-orphans",
        ]
        for call in mock_run.call_args_list
    )


def test_run_command_fails_for_invalid_dspy_optimization_key(tmp_path):
    """Unknown [dspy_optimization] keys should fail config loading."""
    train_app = tmp_path / "train-app"
    train_app.mkdir()

    cfg = tmp_path / "e2e.toml"
    _write_e2e_config(
        cfg,
        [train_app],
        [],
        extra_lines=[
            "[dspy_optimization]",
            'selection_mode = "llm"',
            "not_a_real_field = 1",
        ],
    )

    args = argparse.Namespace(
        config=str(cfg),
        dry_run=False,
        work_dir=str(tmp_path / "work"),
    )

    with patch("app_operator.commands.e2e_optimize.EvalExecuteOptimizer") as mock_eval:
        rc = run_command(args)

    assert rc == 1
    mock_eval.assert_not_called()


def test_init_experiment_normalizes_app_name_for_compose_safe_dir(tmp_path, monkeypatch):
    """Experiment dirs should normalize app names to avoid invalid compose project names."""
    source_app = tmp_path / "HotelReservation"
    source_app.mkdir()
    (source_app / "README.md").write_text("demo app")
    work_dir = tmp_path / "workdir"
    work_dir.mkdir()

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app_operator.commands.e2e_optimize.subprocess.run", _fake_run)

    target = _init_experiment(source_app, work_dir, "iter1_val")

    assert target.name == "hotelreservation_iter1_val"
    assert target.exists()


def test_cleanup_disk_removes_gemini_sessions(tmp_path):
    """Cleanup should remove gemini_sessions dirs from completed runs."""
    work_dir = tmp_path / "workdir"
    work_dir.mkdir()

    # Create fake gemini session dirs in two completed runs
    for name in ("iter1_c1_app", "iter1_c2_app"):
        session_dir = work_dir / name / ".sds" / "trajectories" / "gemini_sessions"
        session_dir.mkdir(parents=True)
        (session_dir / "session1.json").write_text("{}")
        (session_dir / "session2.json").write_text("{}")

    # Non-matching dir should be left alone
    other = work_dir / "iter1_c1_app" / ".sds" / "trajectories" / "trajectory_001.json"
    other.write_text("{}")

    with patch("app_operator.commands.e2e_optimize.subprocess.run"):
        _cleanup_disk_between_iterations(work_dir)

    # Gemini session dirs should be gone
    for name in ("iter1_c1_app", "iter1_c2_app"):
        assert not (work_dir / name / ".sds" / "trajectories" / "gemini_sessions").exists()

    # Trajectory file should still exist
    assert other.exists()
