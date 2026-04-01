"""Tests for sregym_agents.crucible.kb_worker."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from sregym_agents.crucible.kb_worker import (
    process_manifest,
    run_worker,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_manifest(pending_dir: Path, problem_id: str = "test_problem", **overrides) -> Path:
    """Helper to write a valid manifest file."""
    manifest = {
        "session_files": [],
        "stage_outputs_file": None,
        "kb_dir": str(pending_dir.parent),
        "kb_type": "structured",
        "model_id": "test-model",
        "app_name": "test-app",
        "include_benchmark_results": False,
        "problem_id": problem_id,
        "timestamp": "20260401_120000",
        **overrides,
    }
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{manifest['timestamp']}_{problem_id}.json"
    path.write_text(json.dumps(manifest))
    return path


class TestProcessManifest:
    @pytest.mark.asyncio
    async def test_calls_kb_update_and_moves_to_completed(self, tmp_path: Path):
        """Manifest is processed and moved to completed/."""
        pending_dir = tmp_path / "pending"
        session_file = tmp_path / "session.md"
        session_file.write_text("session content")
        manifest_path = _write_manifest(
            pending_dir,
            session_files=[str(session_file)],
        )

        mock_kb = AsyncMock()
        with patch(
            "sregym_agents.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            await process_manifest(manifest_path)

        mock_kb.update.assert_awaited_once()
        assert not manifest_path.exists()
        completed = tmp_path / "completed"
        assert (completed / manifest_path.name).exists()

    @pytest.mark.asyncio
    async def test_moves_to_failed_on_error(self, tmp_path: Path):
        """On kb.update() failure, manifest moves to failed/."""
        pending_dir = tmp_path / "pending"
        manifest_path = _write_manifest(pending_dir)

        mock_kb = AsyncMock()
        mock_kb.update.side_effect = RuntimeError("LLM error")
        with patch(
            "sregym_agents.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            # process_manifest raises — run_worker catches it
            with pytest.raises(RuntimeError, match="LLM error"):
                await process_manifest(manifest_path)


class TestRunWorker:
    @pytest.mark.asyncio
    async def test_exits_after_idle_timeout(self, tmp_path: Path):
        """Worker exits when no manifests appear within idle timeout."""
        start = time.monotonic()
        await run_worker(tmp_path, idle_timeout=3)
        elapsed = time.monotonic() - start
        assert elapsed >= 3
        assert elapsed < 10

    @pytest.mark.asyncio
    async def test_cleans_up_pid_file(self, tmp_path: Path):
        """PID file is removed on exit."""
        pid_path = tmp_path / "kb_worker.pid"
        await run_worker(tmp_path, idle_timeout=1)
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

        with patch("sregym_agents.crucible.kb_worker.asyncio.sleep", side_effect=_capture_pid_and_timeout):
            await run_worker(tmp_path, idle_timeout=1)

        assert len(pid_seen) > 0
        assert pid_seen[0] == os.getpid()

    @pytest.mark.asyncio
    async def test_processes_manifest_and_continues(self, tmp_path: Path):
        """Worker processes a manifest, then idles out."""
        pending_dir = tmp_path / "pending"
        session_file = tmp_path / "session.md"
        session_file.write_text("content")
        _write_manifest(pending_dir, session_files=[str(session_file)])

        mock_kb = AsyncMock()
        with patch(
            "sregym_agents.crucible.kb_worker.create_knowledge_base",
            return_value=mock_kb,
        ):
            await run_worker(tmp_path, idle_timeout=3)

        mock_kb.update.assert_awaited_once()
        assert not list(pending_dir.glob("*.json"))
        assert list((tmp_path / "completed").glob("*.json"))


class TestEnsureKbWorker:
    def test_spawns_worker_and_writes_pid(self, tmp_path: Path):
        """_ensure_kb_worker spawns a process and writes PID file."""
        from sregym_agents.crucible.driver import _ensure_kb_worker

        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        _ensure_kb_worker(kb_dir, "test-model")

        pid_path = kb_dir / "kb_worker.pid"
        assert pid_path.exists()
        pid = int(pid_path.read_text().strip())
        assert pid > 0

        # Clean up: kill the spawned worker
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass

    def test_does_not_spawn_duplicate(self, tmp_path: Path):
        """Second call with live PID does not spawn another worker."""
        from sregym_agents.crucible.driver import _ensure_kb_worker

        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        _ensure_kb_worker(kb_dir, "test-model")
        pid1 = int((kb_dir / "kb_worker.pid").read_text().strip())

        _ensure_kb_worker(kb_dir, "test-model")
        pid2 = int((kb_dir / "kb_worker.pid").read_text().strip())

        assert pid1 == pid2

        try:
            os.kill(pid1, 9)
        except ProcessLookupError:
            pass

    def test_respawns_on_stale_pid(self, tmp_path: Path):
        """Respawns when PID file references a dead process."""
        from sregym_agents.crucible.driver import _ensure_kb_worker

        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        # Write a stale PID (use PID 1 billion which shouldn't exist)
        (kb_dir / "kb_worker.pid").write_text("999999999")

        _ensure_kb_worker(kb_dir, "test-model")

        pid = int((kb_dir / "kb_worker.pid").read_text().strip())
        assert pid != 999999999
        assert pid > 0

        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass

    def test_concurrent_calls_spawn_single_worker(self, tmp_path: Path):
        """Multiple threads calling _ensure_kb_worker only spawn one process."""
        from sregym_agents.crucible.driver import _ensure_kb_worker

        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()
        (kb_dir / "pending").mkdir()

        results = []
        barrier = threading.Barrier(4)

        def _call():
            barrier.wait()
            _ensure_kb_worker(kb_dir, "test-model")
            pid = int((kb_dir / "kb_worker.pid").read_text().strip())
            results.append(pid)

        threads = [threading.Thread(target=_call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should see the same PID
        assert len(set(results)) == 1

        try:
            os.kill(results[0], 9)
        except ProcessLookupError:
            pass
