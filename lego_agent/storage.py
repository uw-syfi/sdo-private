import asyncio
import threading
import time
from pathlib import Path


class LegoAgentStorage:
    """Handles storage of generated scripts."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.current_run_dir = None
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()

    async def _ensure_run_dir_async(self) -> Path:
        async with self._lock:
            current_timestamp = time.strftime("%Y%m%d-%H%M%S")

            # Create new directory if we don't have one or timestamp changed
            if self.current_run_dir is None or self.current_run_dir.name != current_timestamp:
                self.current_run_dir = self.base_dir / current_timestamp
                self.current_run_dir.mkdir(parents=True, exist_ok=True)
        return self.current_run_dir

    def _ensure_run_dir(self) -> Path:
        """Synchronous fallback for non-async callers."""
        with self._thread_lock:
            current_timestamp = time.strftime("%Y%m%d-%H%M%S")
            if self.current_run_dir is None or self.current_run_dir.name != current_timestamp:
                self.current_run_dir = self.base_dir / current_timestamp
                self.current_run_dir.mkdir(parents=True, exist_ok=True)
            return self.current_run_dir

    def write_config(self, config_text: str) -> Path:
        """Write the YAML configuration to storage."""
        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "lego_agent_config.yaml"
        path.write_text(config_text)
        return path

    def write_script(self, script_text: str) -> Path:
        """Write the script to a timestamped directory."""
        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        script_path = run_dir / "generated_script.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text)

        return script_path
