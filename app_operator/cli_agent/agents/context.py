from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import BaseCodingAgent

    from app_operator.dspy_integration import DSPyConfig

from app_operator.core import FileSystemInterface, NullOperatorUI, OperatorConfig, OperatorUI, RealFilesystem
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    TrajectoryRecorderProtocol,
)


@dataclass
class AgentContext:
    """Shared context passed to all agents, eliminating constructor duplication."""

    repo_path: Path
    coding_agent: BaseCodingAgent
    filesystem: FileSystemInterface = field(default_factory=RealFilesystem)
    operator_config: OperatorConfig = field(default_factory=OperatorConfig)
    recorder: TrajectoryRecorderProtocol = field(default_factory=NullTrajectoryRecorder)
    dspy_config: DSPyConfig | None = None
    ui: OperatorUI = field(default_factory=NullOperatorUI)

    @property
    def sds_dir(self) -> Path:
        return self.repo_path / ".sds"
