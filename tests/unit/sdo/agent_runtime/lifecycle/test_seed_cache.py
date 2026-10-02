from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sdo.agent_runtime.lifecycle.seed_cache import (
    CACHE_DIR_ENV,
    LifecycleSeedCache,
    SeedInputs,
    lifecycle_code_digest,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _source_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "--quiet", "--initial-branch=main")
    (path / "main.go").write_text("package main\n", encoding="utf-8")
    _git(path, "add", "--all")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "--quiet", "-m", "source")
    return path


def _add_lifecycle(repo: Path) -> None:
    sdo = repo / ".sdo"
    (sdo / "diagnostics").mkdir(parents=True)
    (sdo / "goal.md").write_text("goal\n", encoding="utf-8")
    (sdo / "diagnostics" / "manifest.yaml").write_text("detectors: []\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "--quiet", "-m", "lifecycle")


def _inputs(**overrides: object) -> SeedInputs:
    values: dict[str, object] = {
        "health_objective": "frontend answers",
        "active_resources": (("Deployment", "frontend"), ("Service", "frontend")),
        "validator_identity": "container-sandbox/v1:sha256:aaa",
        "provider": "codex",
        "model": "gpt-6-luna",
        "code_digest": "code-1",
    }
    values.update(overrides)
    return SeedInputs(**values)  # type: ignore[arg-type]


def test_key_names_every_input_that_can_change_the_lifecycle(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    base = cache.key(repo, _inputs())

    assert cache.key(repo, _inputs()) == base
    for change in (
        {"health_objective": "frontend and search answer"},
        {"active_resources": (("Deployment", "frontend"),)},
        {"validator_identity": "container-sandbox/v1:sha256:bbb"},
        {"provider": "claude"},
        {"model": "other-model"},
        {"code_digest": "code-2"},
    ):
        assert cache.key(repo, _inputs(**change)) != base, change

    (repo / "main.go").write_text("package main\n// changed\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "--quiet", "-am", "source change")
    assert cache.key(repo, _inputs()) != base


def test_active_resource_order_does_not_change_the_key(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    forward = _inputs(active_resources=(("Deployment", "a"), ("Service", "b")))
    backward = _inputs(active_resources=(("Service", "b"), ("Deployment", "a")))

    assert cache.key(repo, forward) == cache.key(repo, backward)


def test_a_stored_lifecycle_restores_into_a_fresh_clone_as_one_commit(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    first = _source_repo(tmp_path / "first")
    key = cache.key(first, _inputs())
    _add_lifecycle(first)
    cache.store(first, key)

    second = tmp_path / "second"
    subprocess.run(["git", "clone", "--quiet", str(first), str(second)], check=True)
    _git(second, "reset", "--quiet", "--hard", "HEAD~1")  # a fresh checkout of the source commit
    head_before = _git(second, "rev-parse", "HEAD")
    assert not (second / ".sdo").exists()

    restored = cache.restore(second, key)

    assert restored is not None
    assert (second / ".sdo" / "goal.md").read_text(encoding="utf-8") == "goal\n"
    assert (second / ".sdo" / "diagnostics" / "manifest.yaml").is_file()
    assert _git(second, "rev-list", "--count", f"{head_before}..HEAD") == "1"
    assert _git(second, "status", "--porcelain") == ""
    assert (second / "main.go").read_text(encoding="utf-8") == "package main\n"
    assert cache.last_event == "hit"


def test_a_missing_entry_is_a_miss(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")

    assert cache.restore(repo, cache.key(repo, _inputs())) is None
    assert cache.last_event == "miss"
    assert not (repo / ".sdo").exists()


def test_restore_never_overwrites_an_existing_lifecycle(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    key = cache.key(repo, _inputs())
    _add_lifecycle(repo)
    other = _source_repo(tmp_path / "other")
    _add_lifecycle(other)
    cache.store(other, cache.key(other, _inputs()))

    assert cache.eligible(repo) is False
    assert cache.restore(repo, key) is None
    assert cache.last_event == "skipped-existing-lifecycle"


def test_reverting_a_restore_returns_the_workspace_to_the_source_commit(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    first = _source_repo(tmp_path / "first")
    key = cache.key(first, _inputs())
    _add_lifecycle(first)
    cache.store(first, key)
    second = tmp_path / "second"
    subprocess.run(["git", "clone", "--quiet", str(first), str(second)], check=True)
    _git(second, "reset", "--quiet", "--hard", "HEAD~1")
    head_before = _git(second, "rev-parse", "HEAD")

    restored = cache.restore(second, key)
    assert restored is not None
    restored.revert()

    assert _git(second, "rev-parse", "HEAD") == head_before
    assert not (second / ".sdo").exists()
    assert _git(second, "status", "--porcelain") == ""
    assert cache.last_event == "restore-rejected"


def test_store_keeps_the_first_entry_for_a_key(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")
    key = cache.key(repo, _inputs())
    _add_lifecycle(repo)
    cache.store(repo, key)
    (repo / ".sdo" / "goal.md").write_text("a later goal\n", encoding="utf-8")

    cache.store(repo, key)

    entry = next((tmp_path / "cache").glob("*/sdo/goal.md"))
    assert entry.read_text(encoding="utf-8") == "goal\n"
    assert not list((tmp_path / "cache").glob(".tmp-*"))


def test_store_refuses_a_workspace_without_a_lifecycle(tmp_path: Path) -> None:
    cache = LifecycleSeedCache(tmp_path / "cache")
    repo = _source_repo(tmp_path / "repo")

    with pytest.raises(ValueError, match="no .sdo"):
        cache.store(repo, cache.key(repo, _inputs()))


def test_cache_directory_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        LifecycleSeedCache(Path("relative"))
    with pytest.raises(TypeError):
        LifecycleSeedCache("/tmp/x")  # type: ignore[arg-type]


def test_from_env_is_opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(CACHE_DIR_ENV, raising=False)
    assert LifecycleSeedCache.from_env() is None

    monkeypatch.setenv(CACHE_DIR_ENV, str(tmp_path / "seeds"))
    cache = LifecycleSeedCache.from_env()
    assert cache is not None
    assert cache.directory == tmp_path / "seeds"


def test_code_digest_covers_the_lifecycle_package_sources() -> None:
    digest = lifecycle_code_digest()

    assert len(digest) == 64
    assert lifecycle_code_digest() == digest
