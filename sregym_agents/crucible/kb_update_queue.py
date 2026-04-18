"""KB update task queue: enqueue/dequeue paths and worker lifecycle.

Each JSON file under ``<kb_dir>/pending/`` is a *KB update task* describing
the incident record files and KB settings for one ``kb_worker`` review run.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_DRAIN_TIMEOUT = 1800.0
DEFAULT_QUEUE_DRAIN_POLL_INTERVAL = 2.0


class KbUpdateTask(BaseModel):
    """Typed, validated payload for a pending KB update task.

    Enforced at the queue boundary: :func:`enqueue_task` accepts only instances
    of this model, and :func:`kb_worker.process_task` validates the JSON on
    disk against this schema before use. Unknown or misspelled fields are
    rejected so typos surface at enqueue/dequeue time rather than silently
    corrupting a KB update at worker runtime.
    """

    model_config = ConfigDict(extra="forbid")

    # Session files (paths as strings; ``None`` when the stage produced nothing).
    diagnosis_run_file: str | None = None
    recovery_diagnosis_run_file: str | None = None
    diagnosis_playbook_candidate_file: str | None = None
    diagnosis_playbook_candidate_origin: str | None = None
    triage_area_candidate_file: str | None = None
    mitigation_run_file: str | None = None
    recovery_mitigation_run_file: str | None = None
    mitigation_playbook_candidate_file: str | None = None
    mitigation_playbook_candidate_origin: str | None = None
    stage_outputs_file: str | None = None

    # KB + run identity (required).
    kb_dir: str
    kb_type: str
    model_id: str
    app_name: str
    include_benchmark_results: bool
    kb_scope: str
    kb_runtime_mode: str
    kb_update_mode: str
    problem_id: str
    prompt_version: str
    diagnosis_succeeded: bool
    mitigation_succeeded: bool

    # Stamped by :func:`enqueue_task` when absent.
    timestamp: str | None = None


@dataclass(frozen=True)
class KbQueuePaths:
    """Filesystem layout for the KB update task queue under *kb_dir*."""

    kb_dir: Path

    @property
    def reviews_root(self) -> Path:
        return self.kb_dir / "v3" / "reviews"

    @property
    def pending(self) -> Path:
        return self.reviews_root / "pending"

    @property
    def completed(self) -> Path:
        return self.reviews_root / "completed"

    @property
    def failed(self) -> Path:
        return self.reviews_root / "failed"

    @property
    def lock_path(self) -> Path:
        return self.reviews_root / "kb_worker.lock"

    @property
    def pid_path(self) -> Path:
        return self.reviews_root / "kb_worker.pid"

    @property
    def worker_log(self) -> Path:
        return self.reviews_root / "kb_worker.log"


@dataclass(frozen=True)
class KbQueueSnapshot:
    """Snapshot of queue file names for one KB directory."""

    pending: frozenset[str]
    failed: frozenset[str]


def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with *pid* appears to be running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_kb_worker_alive(kb_dir: Path) -> bool:
    """Return True if the detached KB worker PID file points to a live process."""
    paths = KbQueuePaths(Path(kb_dir))
    if not paths.pid_path.exists():
        return False
    try:
        pid = int(paths.pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    return _pid_is_alive(pid)


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


def snapshot_kb_queue(kb_dir: Path) -> KbQueueSnapshot:
    """Capture the current queue state for later diffing."""
    paths = KbQueuePaths(Path(kb_dir))
    pending: frozenset[str] = (
        frozenset(p.name for p in paths.pending.glob("*.json")) if paths.pending.exists() else frozenset[str]()
    )
    failed: frozenset[str] = (
        frozenset(p.name for p in paths.failed.glob("*.json")) if paths.failed.exists() else frozenset[str]()
    )
    return KbQueueSnapshot(pending=pending, failed=failed)


def wait_for_kb_queue_drain(
    kb_dir: Path,
    *,
    baseline: KbQueueSnapshot | None = None,
    timeout_s: float = DEFAULT_QUEUE_DRAIN_TIMEOUT,
    poll_interval_s: float = DEFAULT_QUEUE_DRAIN_POLL_INTERVAL,
) -> None:
    """Wait until all queue items added after *baseline* leave ``pending/``.

    If new tasks show up in ``failed/``, raises ``RuntimeError``.
    If new tasks are pending but the detached worker is not alive, respawns it.
    """
    kb_dir = Path(kb_dir)
    baseline = baseline or snapshot_kb_queue(kb_dir)
    deadline = time.monotonic() + timeout_s

    while True:
        current = snapshot_kb_queue(kb_dir)
        new_failed = sorted(current.failed - baseline.failed)
        if new_failed:
            raise RuntimeError(f"KB review tasks failed: {', '.join(new_failed)}")

        new_pending = sorted(current.pending - baseline.pending)
        if not new_pending:
            return

        if not is_kb_worker_alive(kb_dir):
            logger.warning("KB worker not running with pending tasks present; respawning for %s", kb_dir)
            ensure_kb_worker(kb_dir)

        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(f"Timed out waiting for KB review tasks: {', '.join(new_pending)}")
        time.sleep(min(poll_interval_s, max(deadline - now, 0.0)))


def enqueue_task(kb_dir: Path, task: KbUpdateTask, *, problem_id: str) -> Path:
    """Write *task* as JSON under ``pending/{timestamp}_{problem_id}.json``.

    Accepts only a validated :class:`KbUpdateTask` — raw dicts are rejected so
    field typos surface at enqueue time, not inside the worker. Stamps
    ``timestamp`` if the caller left it unset (``%Y%m%d_%H%M%S``).
    """
    if not isinstance(task, KbUpdateTask):  # type: ignore[reportUnnecessaryIsInstance]
        raise TypeError(
            f"enqueue_task requires a KbUpdateTask; got {type(task).__name__}. "
            "Construct a KbUpdateTask(...) at the call site."
        )
    body = task.model_dump(mode="json")
    if not body.get("timestamp"):
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
