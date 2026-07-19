"""Tests for benchmarks.sregym.participants.crucible.kb_worker."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from benchmarks.sregym.participants.crucible.kb_update_queue import ensure_kb_worker
from benchmarks.sregym.participants.crucible.kb_worker import (
    process_task,
    run_worker,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_task(pending_dir: Path, problem_id: str = "test_problem", **overrides) -> Path:
    """Helper to write a valid KB update task file."""
    task = {
        "session_files": {"diagnosis": None, "mitigation": None},
        "stage_outputs_file": None,
        "kb_dir": str(pending_dir.parent),
        "kb_type": "structured",
        "model_id": "test-model",
        "app_name": "test-app",
        "include_benchmark_results": False,
        "enable_reflection": True,
        "recovery_phase2_enabled": False,
        "problem_id": problem_id,
        "prompt_version": "v1",
        "timestamp": "20260401_120000",
        **overrides,
    }
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{task['timestamp']}_{problem_id}.json"
    path.write_text(json.dumps(task))
    return path


class TestProcessTask:
    @pytest.mark.asyncio
    async def test_calls_kb_update_and_moves_to_completed(self, tmp_path: Path):
        """Task is processed and moved to completed/."""
        pending_dir = tmp_path / "pending"
        session_file = tmp_path / "session.md"
        session_file.write_text("session content")
        task_path = _write_task(
            pending_dir,
            session_files={"diagnosis": str(session_file), "mitigation": None},
        )

        mock_kb = AsyncMock()
        with patch(
            "benchmarks.sregym.participants.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            await process_task(task_path)

        mock_kb.update.assert_awaited_once()
        assert not task_path.exists()
        completed = tmp_path / "completed"
        assert (completed / task_path.name).exists()

    @pytest.mark.asyncio
    async def test_passes_recovery_reflection_to_kb_update(self, tmp_path: Path):
        pending_dir = tmp_path / "pending"
        task_path = _write_task(
            pending_dir,
            recovery_reflection={
                "summary": "Grounded recovery narrative",
                "stage_failures": [],
                "investigation_observations": ["Observed failing readiness checks"],
            },
        )

        mock_kb = AsyncMock()
        with patch(
            "benchmarks.sregym.participants.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            await process_task(task_path)

        _args, kwargs = mock_kb.update.await_args
        assert kwargs["recovery_reflection"].summary == "Grounded recovery narrative"

    @pytest.mark.asyncio
    async def test_moves_to_failed_on_error(self, tmp_path: Path):
        """On kb.update() failure, process_task raises (run_worker moves to failed/)."""
        pending_dir = tmp_path / "pending"
        task_path = _write_task(pending_dir)

        mock_kb = AsyncMock()
        mock_kb.update.side_effect = RuntimeError("LLM error")
        with patch(
            "benchmarks.sregym.participants.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            with pytest.raises(RuntimeError, match="LLM error"):
                await process_task(task_path)


_FAST_POLL = 0.01  # fast poll interval for tests


class TestRunWorker:
    @pytest.mark.asyncio
    async def test_exits_after_idle_timeout(self, tmp_path: Path):
        """Worker exits when no tasks appear within idle timeout."""
        start = time.monotonic()
        await run_worker(tmp_path, idle_timeout=0.05, poll_interval=_FAST_POLL)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05
        assert elapsed < 5

    @pytest.mark.asyncio
    async def test_cleans_up_pid_file(self, tmp_path: Path):
        """PID file is removed on exit."""
        pid_path = tmp_path / "kb_worker.pid"
        await run_worker(tmp_path, idle_timeout=0.05, poll_interval=_FAST_POLL)
        assert not pid_path.exists()

    @pytest.mark.asyncio
    async def test_writes_pid_file(self, tmp_path: Path):
        """PID file is written on startup."""
        pid_path = tmp_path / "kb_worker.pid"
        pid_seen = []

        original_sleep = asyncio.sleep

        async def _capture_pid_and_timeout(*args, **kwargs):
            if pid_path.exists():
                pid_seen.append(int(pid_path.read_text().strip()))
            await original_sleep(0)

        with patch(
            "benchmarks.sregym.participants.crucible.kb_worker.asyncio.sleep", side_effect=_capture_pid_and_timeout
        ):
            await run_worker(tmp_path, idle_timeout=0.05, poll_interval=_FAST_POLL)

        assert len(pid_seen) > 0
        assert pid_seen[0] == os.getpid()

    @pytest.mark.asyncio
    async def test_processes_task_and_continues(self, tmp_path: Path):
        """Worker processes a task, then idles out."""
        pending_dir = tmp_path / "pending"
        session_file = tmp_path / "session.md"
        session_file.write_text("content")
        _write_task(pending_dir, session_files={"diagnosis": str(session_file), "mitigation": None})

        mock_kb = AsyncMock()
        with patch(
            "benchmarks.sregym.participants.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            await run_worker(tmp_path, idle_timeout=0.05, poll_interval=_FAST_POLL)

        mock_kb.update.assert_awaited_once()
        assert not list(pending_dir.glob("*.json"))
        assert list((tmp_path / "completed").glob("*.json"))


class TestEnsureKbWorker:
    def test_spawns_worker_and_writes_pid(self, tmp_path: Path):
        """ensure_kb_worker spawns a process and writes PID file."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        ensure_kb_worker(kb_dir)

        pid_path = kb_dir / "kb_worker.pid"
        assert pid_path.exists()
        pid = int(pid_path.read_text().strip())
        assert pid > 0

        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass

    def test_does_not_spawn_duplicate(self, tmp_path: Path):
        """Second call with live PID does not spawn another worker."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        ensure_kb_worker(kb_dir)
        pid1 = int((kb_dir / "kb_worker.pid").read_text().strip())

        ensure_kb_worker(kb_dir)
        pid2 = int((kb_dir / "kb_worker.pid").read_text().strip())

        assert pid1 == pid2

        try:
            os.kill(pid1, 9)
        except ProcessLookupError:
            pass

    def test_respawns_on_stale_pid(self, tmp_path: Path):
        """Respawns when PID file references a dead process."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        (kb_dir / "kb_worker.pid").write_text("999999999")

        ensure_kb_worker(kb_dir)

        pid = int((kb_dir / "kb_worker.pid").read_text().strip())
        assert pid != 999999999
        assert pid > 0

        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass

    def test_concurrent_calls_spawn_single_worker(self, tmp_path: Path):
        """Multiple threads calling ensure_kb_worker only spawn one process."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        results = []
        barrier = threading.Barrier(4)

        def _call():
            barrier.wait()
            ensure_kb_worker(kb_dir)
            pid = int((kb_dir / "kb_worker.pid").read_text().strip())
            results.append(pid)

        threads = [threading.Thread(target=_call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(results)) == 1

        try:
            os.kill(results[0], 9)
        except ProcessLookupError:
            pass
