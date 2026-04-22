from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"

if not BENCH_ROOT.exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping application_workspace tests",
        allow_module_level=True,
    )

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

APP_WORKSPACE_PATH = BENCH_ROOT / "sregym" / "service" / "app_workspace.py"
spec = importlib.util.spec_from_file_location("bench_sregym_app_workspace", APP_WORKSPACE_PATH)
assert spec is not None
assert spec.loader is not None
app_workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_workspace)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_prepare_application_workspace_creates_single_commit_repo(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "sources"
    source_dir = source_root / "hotelReservation"
    (source_dir / "kubernetes").mkdir(parents=True)
    (source_dir / "README.md").write_text("hello\n", encoding="utf-8")
    (source_dir / "kubernetes" / "frontend.yaml").write_text("kind: ConfigMap\n", encoding="utf-8")
    monkeypatch.setattr(app_workspace, "_target_microservices_root", lambda: source_root)

    workspace_dir = app_workspace.prepare_application_workspace(
        experiment_dir=tmp_path / "experiment",
        app_filter="hotel_reservation",
        resume=False,
    )

    assert (workspace_dir / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert (workspace_dir / ".git").is_dir()
    assert _git(["rev-list", "--count", "HEAD"], workspace_dir) == "1"
    assert _git(["log", "-1", "--pretty=%s"], workspace_dir) == "Initial application workspace snapshot"


def test_prepare_application_workspace_reuses_existing_repo_on_resume(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "sources"
    source_dir = source_root / "hotelReservation"
    source_dir.mkdir(parents=True)
    (source_dir / "README.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(app_workspace, "_target_microservices_root", lambda: source_root)

    workspace_dir = app_workspace.prepare_application_workspace(
        experiment_dir=tmp_path / "experiment",
        app_filter="hotel_reservation",
        resume=False,
    )
    (workspace_dir / "README.md").write_text("changed\n", encoding="utf-8")
    _git(["add", "README.md"], workspace_dir)
    _git(
        [
            "-c",
            "user.name=SREGym",
            "-c",
            "user.email=sregym@example.com",
            "commit",
            "-m",
            "agent update",
        ],
        workspace_dir,
    )

    resumed_dir = app_workspace.prepare_application_workspace(
        experiment_dir=tmp_path / "experiment",
        app_filter="hotel_reservation",
        resume=True,
    )

    assert resumed_dir == workspace_dir
    assert (resumed_dir / "README.md").read_text(encoding="utf-8") == "changed\n"
    assert _git(["rev-list", "--count", "HEAD"], resumed_dir) == "2"


def test_should_replay_completed_run_only_when_workspace_mode_enabled():
    assert app_workspace.should_replay_completed_run(
        application_workspace_enabled=True,
        is_resuming=True,
        pending_problems=[],
    )
    assert not app_workspace.should_replay_completed_run(
        application_workspace_enabled=False,
        is_resuming=True,
        pending_problems=[],
    )
    assert not app_workspace.should_replay_completed_run(
        application_workspace_enabled=True,
        is_resuming=True,
        pending_problems=["wrong_service_selector_hotel_reservation"],
    )
    assert not app_workspace.should_replay_completed_run(
        application_workspace_enabled=True,
        is_resuming=False,
        pending_problems=[],
    )
