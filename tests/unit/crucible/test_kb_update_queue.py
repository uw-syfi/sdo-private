"""Tests for sregym_agents.crucible.kb_update_queue."""

from __future__ import annotations

import json
from pathlib import Path

from sregym_agents.crucible.kb_update_queue import (
    KbQueuePaths,
    enqueue_task,
    list_pending_tasks,
    move_to_completed,
    move_to_failed,
)


def test_kb_queue_paths() -> None:
    kb = Path("/tmp/kb_root")
    p = KbQueuePaths(kb)
    assert p.pending == kb / "pending"
    assert p.completed == kb / "completed"
    assert p.failed == kb / "failed"
    assert p.lock_path == kb / "kb_worker.lock"
    assert p.pid_path == kb / "kb_worker.pid"
    assert p.worker_log == kb / "kb_worker.log"


def test_enqueue_task_writes_json_and_sets_timestamp(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    payload = {
        "session_files": {"diagnosis": "/x.md", "mitigation": None},
        "stage_outputs_file": None,
        "kb_dir": str(kb_dir),
        "kb_type": "structured",
        "model_id": "m",
        "app_name": "app",
        "include_benchmark_results": False,
        "enable_heuristic_refinement": True,
        "include_incident_files": True,
        "problem_id": "p1",
        "prompt_version": "v1",
    }
    path = enqueue_task(kb_dir, payload, problem_id="p1")
    assert path.parent == kb_dir / "pending"
    assert path.name.endswith("_p1.json")
    data = json.loads(path.read_text())
    assert "timestamp" in data
    assert data["model_id"] == "m"


def test_enqueue_task_preserves_existing_timestamp(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    payload = {
        "session_files": {"diagnosis": "/x.md", "mitigation": None},
        "stage_outputs_file": None,
        "kb_dir": str(kb_dir),
        "kb_type": "structured",
        "model_id": "m",
        "app_name": "app",
        "include_benchmark_results": False,
        "enable_heuristic_refinement": True,
        "include_incident_files": True,
        "problem_id": "p1",
        "prompt_version": "v1",
        "timestamp": "20260101_000000",
    }
    path = enqueue_task(kb_dir, payload, problem_id="p1")
    assert path.name == "20260101_000000_p1.json"


def test_list_pending_tasks_sorted(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    pending = kb_dir / "pending"
    pending.mkdir(parents=True)
    (pending / "z_9.json").write_text("{}")
    (pending / "a_1.json").write_text("{}")
    (pending / "m_5.json").write_text("{}")
    paths = list_pending_tasks(kb_dir)
    assert [p.name for p in paths] == ["a_1.json", "m_5.json", "z_9.json"]


def test_move_to_completed(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    pending = kb_dir / "pending"
    pending.mkdir(parents=True)
    task = pending / "t1.json"
    task.write_text("{}")
    dest = move_to_completed(task, kb_dir)
    assert dest == kb_dir / "completed" / "t1.json"
    assert dest.exists()
    assert not task.exists()


def test_move_to_failed(tmp_path: Path) -> None:
    kb_dir = tmp_path / "kb"
    pending = kb_dir / "pending"
    pending.mkdir(parents=True)
    task = pending / "t1.json"
    task.write_text("{}")
    dest = move_to_failed(task, kb_dir)
    assert dest == kb_dir / "failed" / "t1.json"
    assert dest.exists()
    assert not task.exists()
