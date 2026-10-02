from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.adapter import driver
from sdo.agent_runtime.lifecycle.seed_cache import LifecycleSeedCache

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _source_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "--quiet", "--initial-branch=main")
    (path / "main.go").write_text("package main\n", encoding="utf-8")
    _git(path, "add", "--all")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "--quiet", "-m", "source")
    return path


def _author_lifecycle(repo: Path) -> None:
    (repo / ".sdo").mkdir()
    (repo / ".sdo" / "goal.md").write_text("goal\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "--quiet", "-m", "lifecycle")


def _context() -> driver.DeployedLifecycleContext:
    return driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[])


@pytest.fixture(autouse=True)
def _validator_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, "validator_identity", lambda image: f"identity-of-{image}")


def _run(repo: Path, cache: LifecycleSeedCache | None) -> bool:
    return driver.run_or_reuse_lifecycle(
        repo,
        application="demo",
        context=_context(),
        provider="codex",
        model="gpt-6-luna",
        validator_image="validator:t1",
        seed_cache=cache,
    )


def test_a_cold_lifecycle_is_stored_for_the_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "first")
    cold_runs: list[Path] = []

    def cold(root: Path, **_kwargs: object) -> str:
        cold_runs.append(root)
        _author_lifecycle(root)
        return "commit"

    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", cold)

    assert _run(repo, cache) is False

    assert cold_runs == [repo]
    assert list((tmp_path / "cache").glob("*/sdo/goal.md"))


def test_a_stored_lifecycle_replaces_the_cold_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    first = _source_repo(tmp_path / "first")
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda root, **_k: _author_lifecycle(root))
    _run(first, cache)

    second = tmp_path / "second"
    subprocess.run(["git", "clone", "--quiet", str(first), str(second)], check=True)
    _git(second, "reset", "--quiet", "--hard", "HEAD~1")
    cold_runs: list[Path] = []
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda root, **_k: cold_runs.append(root))
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: (second / ".sdo").is_dir())

    reused = _run(second, cache)

    assert reused is True
    assert cold_runs == []
    assert (second / ".sdo" / "goal.md").is_file()
    assert cache.last_event == "hit"


def test_a_restored_lifecycle_that_fails_validation_is_reverted_and_authored_cold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    first = _source_repo(tmp_path / "first")
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda root, **_k: _author_lifecycle(root))
    _run(first, cache)
    second = tmp_path / "second"
    subprocess.run(["git", "clone", "--quiet", str(first), str(second)], check=True)
    _git(second, "reset", "--quiet", "--hard", "HEAD~1")
    head_before = _git(second, "rev-parse", "HEAD")
    seen_at_cold_start: list[bool] = []

    def cold(root: Path, **_kwargs: object) -> str:
        seen_at_cold_start.append((root / ".sdo").exists() and _git(root, "rev-parse", "HEAD") != head_before)
        return "commit"

    monkeypatch.setattr(driver, "run_initial_lifecycle", cold)

    reused = _run(second, cache)

    assert reused is False
    assert seen_at_cold_start == [False]
    assert _git(second, "rev-parse", "HEAD") == head_before


def test_a_workspace_with_a_lifecycle_never_touches_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    _author_lifecycle(repo)
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: True)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda *_a, **_k: pytest.fail("cold lifecycle must not run"))

    assert _run(repo, cache) is True

    assert not (tmp_path / "cache").exists()


def test_a_validator_without_an_identity_disables_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    monkeypatch.setattr(driver, "validator_identity", lambda image: None)
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda root, **_k: _author_lifecycle(root))

    assert _run(repo, cache) is False

    assert not (tmp_path / "cache").exists()


def test_no_cache_leaves_the_flow_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _source_repo(tmp_path / "repo")
    cold: list[Path] = []
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_a, **_k: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda root, **_k: cold.append(root))

    assert _run(repo, None) is False

    assert cold == [repo]
