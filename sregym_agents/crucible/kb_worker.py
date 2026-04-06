"""Dedicated KB update queue worker.

Watches ``<kb_dir>/pending/`` for KB update task JSON files and processes them
one at a time, eliminating timeout and concurrency issues that arise when
KB updates run inline in the agent process.

Usage::

    python -m sregym_agents.crucible.kb_worker --kb-dir /path/to/kb
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import crucible_config_from_kb_task
from sregym_agents.crucible.kb_update_queue import (
    list_pending_tasks,
    move_to_completed,
    move_to_failed,
)
from sregym_agents.crucible.knowledge_base import SessionFiles, create_knowledge_base

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2  # seconds
DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes
_TASK_MAX_RETRIES = 3
_TASK_RETRY_DELAY = 5.0


async def process_task(task_path: Path) -> None:
    """Load a task file, create KB, call ``update()``, move to ``completed/``."""
    task = json.loads(task_path.read_text())

    crucible_config = crucible_config_from_kb_task(task)
    renderer = PromptRenderer(crucible_config.prompt_version)

    sf = task["session_files"]
    session_files = SessionFiles(
        diagnosis=Path(sf["diagnosis"]) if sf.get("diagnosis") else None,
        mitigation=Path(sf["mitigation"]) if sf.get("mitigation") else None,
    )
    stage_outputs_file = Path(task["stage_outputs_file"]) if task.get("stage_outputs_file") else None

    kb = create_knowledge_base(
        kb_type=task["kb_type"],
        kb_dir=Path(task["kb_dir"]),
        model_id=task["model_id"],
        app_name=task["app_name"],
        config=crucible_config,
        renderer=renderer,
    )

    logger.info(
        "Processing KB update for %s (%s)",
        task["problem_id"],
        task["app_name"],
    )
    await kb.update(session_files, stage_outputs_file=stage_outputs_file)
    logger.info("KB update complete for %s", task["problem_id"])

    kb_dir = Path(task["kb_dir"])
    move_to_completed(task_path, kb_dir)


async def run_worker(
    kb_dir: Path,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Poll ``pending/`` and process tasks sequentially.

    Exits after *idle_timeout* seconds with no new tasks.
    """
    pending_dir = kb_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pid_path = kb_dir / "kb_worker.pid"

    logger.info("KB worker started (pid=%d), watching %s", os.getpid(), pending_dir)

    try:
        pid_path.write_text(str(os.getpid()))
        last_activity = time.monotonic()

        while True:
            tasks = list_pending_tasks(kb_dir)

            if tasks:
                last_activity = time.monotonic()

            for m in tasks:
                succeeded = False
                for attempt in range(_TASK_MAX_RETRIES):
                    try:
                        await process_task(m)
                        succeeded = True
                        break
                    except Exception:
                        logger.error(
                            "KB update failed for %s (attempt %d/%d)",
                            m.name,
                            attempt + 1,
                            _TASK_MAX_RETRIES,
                            exc_info=True,
                        )
                        if attempt < _TASK_MAX_RETRIES - 1:
                            await asyncio.sleep(_TASK_RETRY_DELAY * (2**attempt))
                if not succeeded:
                    move_to_failed(m, kb_dir)

            if time.monotonic() - last_activity > idle_timeout:
                logger.info("KB worker idle for %.1fs, exiting.", idle_timeout)
                break

            await asyncio.sleep(poll_interval)
    finally:
        pid_path.unlink(missing_ok=True)
        logger.info("KB worker exiting, PID file cleaned up.")


def _setup_signal_handlers() -> None:
    """Ensure SIGTERM triggers a clean shutdown via KeyboardInterrupt."""

    def _handler(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="KB update queue worker")
    parser.add_argument("--kb-dir", required=True, type=Path)
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=DEFAULT_IDLE_TIMEOUT,
        help="Exit after this many seconds idle (default: %(default)s)",
    )
    args = parser.parse_args()

    # Set up logging to file only — stdout/stderr are already redirected to this
    # file by the spawner (ensure_kb_worker in kb_update_queue), so a StreamHandler would duplicate.
    log_path = args.kb_dir / "kb_worker.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(handler)

    _setup_signal_handlers()

    try:
        asyncio.run(run_worker(args.kb_dir, idle_timeout=args.idle_timeout))
    except KeyboardInterrupt:
        logger.info("KB worker interrupted.")


if __name__ == "__main__":
    main()
