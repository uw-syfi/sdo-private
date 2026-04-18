from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from sregym_agents.crucible.kb_update_queue import (
    KbUpdateTask,
    enqueue_task,
    snapshot_kb_queue,
    wait_for_kb_queue_drain,
)
from sregym_agents.crucible.knowledge_base import seed_kb

if TYPE_CHECKING:
    from pathlib import Path


def _minimal_task_kwargs(kb_dir: Path) -> dict[str, Any]:
    """Return the minimum set of required KbUpdateTask fields for tests."""
    return {
        "kb_dir": str(kb_dir),
        "kb_type": "structured",
        "model_id": "test-model",
        "app_name": "social-network",
        "include_benchmark_results": True,
        "kb_scope": "per_app",
        "kb_runtime_mode": "playbook-first",
        "kb_update_mode": "async-review",
        "problem_id": "problem-1",
        "prompt_version": "v3",
        "diagnosis_succeeded": False,
        "mitigation_succeeded": False,
    }


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


def test_kb_update_task_rejects_missing_required_fields(tmp_path: Path) -> None:
    # Drop a required field (model_id) and expect validation to fail.
    kwargs = _minimal_task_kwargs(tmp_path)
    del kwargs["model_id"]
    with pytest.raises(ValidationError):
        KbUpdateTask(**kwargs)


def test_kb_update_task_rejects_unknown_fields(tmp_path: Path) -> None:
    kwargs = _minimal_task_kwargs(tmp_path)
    kwargs["surprise_field"] = "oops"
    with pytest.raises(ValidationError):
        KbUpdateTask(**kwargs)


def test_kb_update_task_accepts_well_formed_input(tmp_path: Path) -> None:
    task = KbUpdateTask(**_minimal_task_kwargs(tmp_path))
    # All file-path fields default to None; timestamp optional.
    assert task.diagnosis_run_file is None
    assert task.triage_area_candidate_file is None
    assert task.stage_outputs_file is None
    assert task.timestamp is None


def test_enqueue_task_writes_validated_payload(tmp_path: Path) -> None:
    task = KbUpdateTask(**_minimal_task_kwargs(tmp_path))
    path = enqueue_task(tmp_path, task, problem_id="problem-1")
    assert path.exists()
    body = json.loads(path.read_text())
    assert body["problem_id"] == "problem-1"
    assert body["kb_type"] == "structured"
    # enqueue_task stamps a timestamp when missing.
    assert "timestamp" in body
    assert body["timestamp"]


def test_enqueue_task_rejects_plain_dict(tmp_path: Path) -> None:
    # Raw dicts are no longer accepted: callers must construct a typed KbUpdateTask.
    payload = _minimal_task_kwargs(tmp_path)
    with pytest.raises((TypeError, AttributeError)):
        enqueue_task(tmp_path, payload, problem_id="problem-1")  # type: ignore[arg-type]


def test_enqueue_to_process_task_roundtrip(tmp_path: Path) -> None:
    """Enqueued JSON passes the KbUpdateTask validator used by kb_worker.process_task."""
    task = KbUpdateTask(**_minimal_task_kwargs(tmp_path))
    path = enqueue_task(tmp_path, task, problem_id="problem-1")
    loaded = KbUpdateTask.model_validate_json(path.read_text())
    assert loaded.problem_id == "problem-1"
    assert loaded.timestamp is not None


def test_process_task_rejects_unknown_field_in_queue_file(tmp_path: Path) -> None:
    """kb_worker.process_task refuses to run a JSON with a renamed/unknown field."""
    import asyncio as _asyncio

    from sregym_agents.crucible import kb_worker
    from sregym_agents.crucible.kb_update_queue import KbQueuePaths

    paths = KbQueuePaths(tmp_path)
    paths.pending.mkdir(parents=True, exist_ok=True)
    task_path = paths.pending / "broken.json"
    body = _minimal_task_kwargs(tmp_path)
    body["surprise_field"] = "nope"  # unknown field must be rejected
    task_path.write_text(json.dumps(body))

    with pytest.raises(ValidationError):
        _asyncio.run(kb_worker.process_task(task_path))
