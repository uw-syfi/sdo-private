from __future__ import annotations

from typing import TYPE_CHECKING

from sregym_agents.crucible.kb_update_queue import snapshot_kb_queue, wait_for_kb_queue_drain
from sregym_agents.crucible.knowledge_base import seed_kb

if TYPE_CHECKING:
    from pathlib import Path


def test_seed_kb_skips_runtime_queue_artifacts(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"

    reusable = src / "v3" / "apps" / "social-network" / "root_causes" / "dns-failure" / "diagnosis.md"
    reusable.parent.mkdir(parents=True, exist_ok=True)
    reusable.write_text("playbook")

    runtime_files = [
        src / "kb_worker.log",
        src / "kb_worker.pid",
        src / "pending" / "legacy.json",
        src / "v3" / "reviews" / "pending" / "task.json",
        src / "v3" / "reviews" / "kb_worker.lock",
    ]
    for path in runtime_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime")

    seed_kb(dest, src)

    assert (dest / "v3" / "apps" / "social-network" / "root_causes" / "dns-failure" / "diagnosis.md").exists()
    assert not (dest / "kb_worker.log").exists()
    assert not (dest / "kb_worker.pid").exists()
    assert not (dest / "pending" / "legacy.json").exists()
    assert not (dest / "v3" / "reviews" / "pending" / "task.json").exists()
    assert not (dest / "v3" / "reviews" / "kb_worker.lock").exists()


def test_wait_for_kb_queue_drain_respawns_worker_for_pending_tasks(tmp_path: Path, monkeypatch) -> None:
    kb_dir = tmp_path / "kb"
    baseline = snapshot_kb_queue(kb_dir)

    pending_task = kb_dir / "v3" / "reviews" / "pending" / "task.json"
    pending_task.parent.mkdir(parents=True, exist_ok=True)
    pending_task.write_text("{}")

    ensured: list[Path] = []
    alive_checks = {"count": 0}

    def fake_is_alive(path: Path) -> bool:
        alive_checks["count"] += 1
        return False

    def fake_ensure(path: Path) -> None:
        ensured.append(path)

    def fake_sleep(_seconds: float) -> None:
        pending_task.unlink()

    monkeypatch.setattr("sregym_agents.crucible.kb_update_queue.is_kb_worker_alive", fake_is_alive)
    monkeypatch.setattr("sregym_agents.crucible.kb_update_queue.ensure_kb_worker", fake_ensure)
    monkeypatch.setattr("sregym_agents.crucible.kb_update_queue.time.sleep", fake_sleep)

    wait_for_kb_queue_drain(kb_dir, baseline=baseline, timeout_s=1.0, poll_interval_s=0.01)

    assert alive_checks["count"] >= 1
    assert ensured == [kb_dir]
