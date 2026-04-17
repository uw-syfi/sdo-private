"""Dependency injection container for pydantic_ai operator."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app_operator.core import Config, FileSystemInterface
from app_operator.prompts import PromptLoader


@dataclass
class OperatorDeps:
    repo_path: Path
    filesystem: FileSystemInterface
    loader: PromptLoader
    config: Config
    check_shutdown: Callable[[], bool] | None = None
    _tool_call_counter: int = field(default=0, repr=False)
    _counter_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def should_shutdown(self) -> bool:
        return bool(self.check_shutdown and self.check_shutdown())

    def resolve_path(self, path: str) -> Path:
        """Resolve a path relative to repo_path, preventing escapes."""
        path_obj = Path(path)
        if path_obj.is_absolute():
            candidate = path_obj
        else:
            candidate = self.repo_path / path

        # Normalize .. and . without filesystem access
        parts: list[str] = []
        for part in candidate.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)

        candidate = Path(*parts) if parts else Path("/")

        # Check if path escapes repository root
        try:
            candidate.relative_to(self.repo_path)
        except ValueError as err:
            raise ValueError(f"Path escapes repository root: {path}") from err

        return candidate

    def next_tool_call_id(self) -> int:
        with self._counter_lock:
            self._tool_call_counter += 1
            return self._tool_call_counter
