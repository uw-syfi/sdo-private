from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface
from app_operator.prompts import PromptLoader
from app_operator.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol


@dataclass
class NodeContext:
    repo_path: Path
    filesystem: FileSystemInterface
    loader: PromptLoader
    config: Config
    context_limit: int
    recorder: TrajectoryRecorderProtocol
    health_check_interval: int
    check_shutdown: Callable[[], bool] | None = None
    analyze_agent: Any = None
    script_agent: Any = None
    fix_agent: Any = None
    health_agent: Any = None
    consolidation_agent: Any = None

    def __post_init__(self) -> None:
        if self.recorder is None:
            self.recorder = NullTrajectoryRecorder()

    def should_shutdown(self) -> bool:
        return bool(self.check_shutdown and self.check_shutdown())
