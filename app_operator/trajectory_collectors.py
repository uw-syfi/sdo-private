"""
Trajectory collectors for SDS.
"""

import shutil
import time
from pathlib import Path

from app_operator.logger import logger

# Gemini CLI session storage location
GEMINI_SESSION_DIR = Path.home() / ".gemini" / "tmp"


def collect_gemini_sessions(
    sds_dir: Path,
    trajectories_dir: Path,
    run_timestamp: str,
    start_time_str: str,
) -> list[str]:
    """Collect and copy recent Gemini CLI session files to the trajectory directory.

    Args:
        sds_dir: The .sds directory.
        trajectories_dir: The .sds/trajectories directory.
        run_timestamp: The timestamp string for the current run.
        start_time_str: The start time string of the run.

    Returns:
        List of relative paths to the copied session files.
    """
    if not GEMINI_SESSION_DIR.exists():
        return []

    sessions_copied = []

    try:
        # Parse as struct_time for comparison
        run_start = time.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        run_start_ts = time.mktime(run_start)

        # Find all session files created after our run started
        gemini_sessions_dir = trajectories_dir / "gemini_sessions" / run_timestamp

        for project_dir in GEMINI_SESSION_DIR.iterdir():
            if not project_dir.is_dir() or project_dir.name == "bin":
                continue

            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue

            for session_file in chats_dir.glob("session-*.json"):
                # Check if file was modified after run started
                file_mtime = session_file.stat().st_mtime
                if file_mtime >= run_start_ts:
                    # Copy session file to our trajectory directory
                    gemini_sessions_dir.mkdir(parents=True, exist_ok=True)
                    dest_file = (
                        gemini_sessions_dir / f"{project_dir.name}_{session_file.name}"
                    )
                    shutil.copy2(session_file, dest_file)
                    sessions_copied.append(str(dest_file.relative_to(sds_dir)))

    except Exception as e:
        logger.warning(f"Failed to collect Gemini sessions: {e}")

    return sessions_copied
