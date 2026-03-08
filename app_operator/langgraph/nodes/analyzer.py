from pathlib import Path
from typing import Any

from app_operator.filesystem import FileSystemInterface
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import invoke_agent
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import PromptLoader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)


def analyze_code(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    loader: PromptLoader,
    agent: Any,
    context_limit: int,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if state["analysis_done"]:
        return state

    with recorder.phase(Phase.EXPLORATION):
        # Check if analysis files already exist
        sds_dir = repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if filesystem.exists(analysis_file) and filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            state["analysis_done"] = True
            return state

        emit_progress("code_analysis")
        system_prompt = loader.render("code_analyzer/system.jinja2")
        user_prompt = loader.render("code_analyzer/user.jinja2", repo_path=repo_path)

        _, messages = invoke_agent(
            state,
            agent,
            system_prompt,
            user_prompt,
            agent_name="Code Analyzer",
            context_limit=context_limit,
            recorder=recorder,
        )

        state["analysis_done"] = True
        state["messages"] = messages
        return state
