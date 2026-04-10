"""KB update task queue: enqueue/dequeue paths and worker lifecycle.

Each JSON file under ``<kb_dir>/pending/`` is a *KB update task* describing
session files and KB settings for one ``KnowledgeBase.update()`` run.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from filelock import FileLock

logger = logging.getLogger(__name__)


class SessionFilesPayload(TypedDict, total=False):
    diagnosis: str | None
    mitigation: str | None


class KbUpdateTaskDict(TypedDict):
    """JSON shape for a pending KB update task (matches worker expectations)."""

    session_files: SessionFilesPayload
    stage_outputs_file: str | None
    kb_dir: str
    kb_type: str
    model_id: str
    app_name: str
    include_benchmark_results: bool
    enable_reflection: bool
    enable_playbooks: bool
    recovery_phase2_enabled: bool
    include_incident_files: bool
    per_app: bool
    problem_id: str
    prompt_version: str
    recovery_reflection: dict[str, Any] | None
    diagnosis_succeeded: bool
    mitigation_succeeded: bool
    timestamp: str


@dataclass(frozen=True)
class KbQueuePaths:
    """Filesystem layout for the KB update task queue under *kb_dir*."""

    kb_dir: Path

    @property
    def pending(self) -> Path:
        return self.kb_dir / "pending"

    @property
    def completed(self) -> Path:
        return self.kb_dir / "completed"

    @property
    def failed(self) -> Path:
        return self.kb_dir / "failed"

    @property
    def lock_path(self) -> Path:
        return self.kb_dir / "kb_worker.lock"

    @property
    def pid_path(self) -> Path:
        return self.kb_dir / "kb_worker.pid"

    @property
    def worker_log(self) -> Path:
        return self.kb_dir / "kb_worker.log"


def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with *pid* appears to be running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def ensure_kb_worker(kb_dir: Path) -> None:
    """Spawn a detached KB worker if one is not already running.

    Uses a file lock to prevent race conditions when multiple driver
    processes start simultaneously.
    """
    paths = KbQueuePaths(kb_dir)

    with FileLock(paths.lock_path, timeout=10):
        if paths.pid_path.exists():
            try:
                pid = int(paths.pid_path.read_text().strip())
            except (ValueError, OSError):
                pid = -1
            if _pid_is_alive(pid):
                logger.info("KB worker already running (pid=%d)", pid)
                return
            logger.info("Stale KB worker PID file (pid=%d), respawning.", pid)
            paths.pid_path.unlink(missing_ok=True)

        log_file = open(paths.worker_log, "a")  # noqa: SIM115
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sregym_agents.crucible.kb_worker",
                "--kb-dir",
                str(kb_dir),
            ],
            cwd=str(kb_dir),
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
        )
        paths.pid_path.write_text(str(proc.pid))
        logger.info("Spawned KB worker (pid=%d), log at %s", proc.pid, paths.worker_log)


def enqueue_task(kb_dir: Path, payload: dict[str, Any], *, problem_id: str) -> Path:
    """Write *payload* as JSON under ``pending/{timestamp}_{problem_id}.json``.

    Sets ``timestamp`` on the serialized body if missing (``%Y%m%d_%H%M%S``).
    """
    body = dict(payload)
    if "timestamp" not in body:
        body["timestamp"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = KbQueuePaths(Path(kb_dir))
    paths.pending.mkdir(parents=True, exist_ok=True)
    filename = f"{body['timestamp']}_{problem_id}.json"
    out = paths.pending / filename
    out.write_text(json.dumps(body, indent=2))
    return out


def list_pending_tasks(kb_dir: Path) -> list[Path]:
    """Return sorted paths to ``*.json`` files in ``pending/``."""
    paths = KbQueuePaths(kb_dir)
    return sorted(paths.pending.glob("*.json"))


def move_to_completed(task_path: Path, kb_dir: Path) -> Path:
    """Move *task_path* to ``completed/``; return the destination path."""
    paths = KbQueuePaths(kb_dir)
    paths.completed.mkdir(exist_ok=True)
    dest = paths.completed / task_path.name
    shutil.move(str(task_path), str(dest))
    return dest


def move_to_failed(task_path: Path, kb_dir: Path) -> Path:
    """Move *task_path* to ``failed/``; return the destination path."""
    paths = KbQueuePaths(kb_dir)
    paths.failed.mkdir(exist_ok=True)
    dest = paths.failed / task_path.name
    shutil.move(str(task_path), str(dest))
    return dest
