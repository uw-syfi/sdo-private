from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface
from app_operator.prompts import PromptLoader
from app_operator.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol

if TYPE_CHECKING:
    from app_operator.langgraph.state import OperatorState
    from app_operator.langgraph.utils import AgentResult


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

    def invoke(
        self,
        state: "OperatorState",
        agent: Any,
        system_prompt: str,
        user_prompt: str,
        agent_name: str = "Agent",
        logger: Any = None,
    ) -> "AgentResult":
        """Invoke an agent with context_limit and recorder pre-filled from this NodeContext."""
        from app_operator.langgraph.utils import invoke_agent
        from app_operator.langgraph.utils import logger as default_logger

        return invoke_agent(
            state,
            agent,
            system_prompt,
            user_prompt,
            agent_name=agent_name,
            context_limit=self.context_limit,
            recorder=self.recorder,
            logger=logger if logger is not None else default_logger,
        )
