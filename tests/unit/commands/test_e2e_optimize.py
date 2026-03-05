"""Unit tests for app_operator.commands.e2e_optimize."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app_operator.commands.e2e_optimize import _init_experiment, run_command


def _write_e2e_config(
    config_path: Path,
    train_apps: list[Path],
    val_apps: list[Path] | None = None,
    iterations: int = 1,
) -> None:
    """Write a minimal e2e config TOML for tests."""
    val_apps = val_apps or []
    train_items = ", ".join(f'"{p}"' for p in train_apps)
    val_items = ", ".join(f'"{p}"' for p in val_apps)
    config_path.write_text(
        "\n".join(
            [
                f"iterations = {iterations}",
                'prompts = ["deployer_fix_error"]',
                '[training]',
                f"apps = [{train_items}]",
                '[validation]',
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
):
    """Build a lightweight app config object for run_command tests."""
    return SimpleNamespace(
        agent=SimpleNamespace(provider=provider, model=model, location=location),
        dspy=SimpleNamespace(
            optimization=SimpleNamespace(
                teacher_model="gemini-2.5-pro",
                n_candidates=n_candidates,
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
        with patch(
            "app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling"
        ) as mock_run:
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
        with patch(
            "app_operator.commands.e2e_optimize.run_subprocess_with_rate_limit_handling"
        ) as mock_run:
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
