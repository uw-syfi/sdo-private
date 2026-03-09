from collections.abc import Callable
from dataclasses import dataclass, field
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
    check_shutdown: Callable[[], bool] | None = None
    subagent_token_sink: list = field(default_factory=list)

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
        from app_operator.langgraph.utils import _record_session_usage, invoke_agent
        from app_operator.langgraph.utils import logger as default_logger

        result = invoke_agent(
            state,
            agent,
            system_prompt,
            user_prompt,
            agent_name=agent_name,
            context_limit=self.context_limit,
            recorder=self.recorder,
            logger=logger if logger is not None else default_logger,
        )
        for record in self.subagent_token_sink:
            _record_session_usage(state, record["agent"], record, self.recorder)
        self.subagent_token_sink.clear()
        return result
