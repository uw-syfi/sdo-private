"""Dedicated KB update queue worker.

Watches ``<kb_dir>/pending/`` for manifest JSON files and processes them
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
import shutil
import signal
import time
from pathlib import Path

from sregym_agents.crucible._prompts import configure as configure_prompts
from sregym_agents.crucible.knowledge_base import create_knowledge_base

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2  # seconds
DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes


async def process_manifest(manifest_path: Path) -> None:
    """Load a manifest, create KB, call ``update()``, move to ``completed/``."""
    manifest = json.loads(manifest_path.read_text())

    prompt_version = manifest.get("prompt_version")
    if prompt_version:
        configure_prompts(prompt_version)

    shared_files = [Path(p) for p in manifest["session_files"]]
    stage_outputs_file = Path(manifest["stage_outputs_file"]) if manifest.get("stage_outputs_file") else None

    kb = create_knowledge_base(
        kb_type=manifest["kb_type"],
        shared_files=shared_files,
        kb_dir=Path(manifest["kb_dir"]),
        model_id=manifest["model_id"],
        app_name=manifest["app_name"],
        include_benchmark_results=manifest.get("include_benchmark_results", False),
    )

    logger.info(
        "Processing KB update for %s (%s)",
        manifest["problem_id"],
        manifest["app_name"],
    )
    await kb.update(stage_outputs_file=stage_outputs_file)
    logger.info("KB update complete for %s", manifest["problem_id"])

    completed_dir = manifest_path.parent.parent / "completed"
    completed_dir.mkdir(exist_ok=True)
    shutil.move(str(manifest_path), str(completed_dir / manifest_path.name))


async def run_worker(kb_dir: Path, idle_timeout: int = DEFAULT_IDLE_TIMEOUT) -> None:
    """Poll ``pending/`` and process manifests sequentially.

    Exits after *idle_timeout* seconds with no new manifests.
    """
    pending_dir = kb_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pid_path = kb_dir / "kb_worker.pid"

    logger.info("KB worker started (pid=%d), watching %s", os.getpid(), pending_dir)

    try:
        pid_path.write_text(str(os.getpid()))
        last_activity = time.monotonic()

        while True:
            manifests = sorted(pending_dir.glob("*.json"))

            if manifests:
                last_activity = time.monotonic()

            for m in manifests:
                try:
                    await process_manifest(m)
                except Exception:
                    logger.error("KB update failed for %s", m.name, exc_info=True)
                    failed_dir = kb_dir / "failed"
                    failed_dir.mkdir(exist_ok=True)
                    shutil.move(str(m), str(failed_dir / m.name))

            if time.monotonic() - last_activity > idle_timeout:
                logger.info("KB worker idle for %ds, exiting.", idle_timeout)
                break

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        pid_path.unlink(missing_ok=True)
        logger.info("KB worker exiting, PID file cleaned up.")


def _setup_signal_handlers() -> None:
    """Ensure SIGTERM triggers a clean shutdown via KeyboardInterrupt."""

    def _handler(signum, frame):
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
    # file by the spawner (_ensure_kb_worker), so a StreamHandler would duplicate.
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
