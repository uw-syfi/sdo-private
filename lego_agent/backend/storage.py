import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any


class LegoAgentStorage:
    """Handles storage of generated scripts."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.current_run_dir: Path | None = None
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._llm_log_lock = threading.Lock()

    def set_run_dir(self, run_dir: Path) -> None:
        """Pin storage to an existing run directory."""
        self.current_run_dir = run_dir
        self.current_run_dir.mkdir(parents=True, exist_ok=True)

    def _create_run_dir(self) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.base_dir / timestamp
        suffix = 1

        while run_dir.exists():
            run_dir = self.base_dir / f"{timestamp}-{suffix}"
            suffix += 1

        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    async def _ensure_run_dir_async(self) -> Path:
        async with self._lock:
            if self.current_run_dir is None:
                self.current_run_dir = self._create_run_dir()
            else:
                self.current_run_dir.mkdir(parents=True, exist_ok=True)
            return self.current_run_dir

    def _ensure_run_dir(self) -> Path:
        """Synchronous fallback for non-async callers."""
        with self._thread_lock:
            if self.current_run_dir is None:
                self.current_run_dir = self._create_run_dir()
            else:
                self.current_run_dir.mkdir(parents=True, exist_ok=True)
            return self.current_run_dir

    def write_config(self, config_text: str) -> Path:
        """Write the YAML configuration to the current run directory."""
        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "lego_agent_config.yaml"
        path.write_text(config_text)
        return path

    def write_script(self, script_text: str) -> Path:
        """Write the generated script to the current run directory."""
        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        script_path = run_dir / "generated_script.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text)

        return script_path

    def log_llm_call(self, event_type: str, details: dict[str, Any]) -> None:
        """Log an LLM call with timestamp and details.

        Args:
            event_type: Type of event (e.g., 'start', 'stream', 'end', 'error')
            details: Dictionary containing event details (model, tokens, etc.)
        """
        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "llm_calls.jsonl"

        log_entry: dict[str, Any] = {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event_type": event_type,
            **details,
        }

        with self._llm_log_lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
