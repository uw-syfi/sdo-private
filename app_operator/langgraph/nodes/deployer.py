import re
from pathlib import Path
from typing import Any, Callable, Optional

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface
from app_operator.prompts import PromptLoader
from app_operator.prompts.deployment_context import create_system_prompt
from app_operator.prompts.deployer import create_fix_prompt, prepare_error_context
from app_operator.trajectory import (
    Phase,
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
)
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import (
    BLUE,
    RESET,
    invoke_agent,
    run_script,
    write_log_file,
)


def deploy_attempt(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    config: Config,
    check_shutdown: Optional[Callable[[], bool]],
    recorder: Optional[TrajectoryRecorderProtocol] = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if check_shutdown and check_shutdown():
        return state

    recorder.start_phase(
        Phase.DEPLOYMENT,
        {"attempt": state["attempt"], "max_attempts": state["max_attempts"]},
    )

    log_file = repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"

    print(f"\n{BLUE}Running deployment script...{RESET}")
    print(f"Logging output to: {log_file}")

    result = run_script(
        repo_path,
        filesystem,
        ".sds/deploy.sh start",
        log_file_path=log_file,
        timeout=config.operator.deploy_timeout,
        recorder=recorder,
    )
    state["deploy_result"] = result
    return state


def fix_errors(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    config: Config,
    loader: PromptLoader,
    agent: Any,
    context_limit: int,
    check_shutdown: Optional[Callable[[], bool]],
    recorder: Optional[TrajectoryRecorderProtocol] = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if check_shutdown and check_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    health_result = state.get("health_result")

    log_file_path = (
        repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"
    )
    health_check_log_path = None
    if health_result:
        health_check_log_path = (
            repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
        )

    error_context = prepare_error_context(
        deploy_result, health_result, log_file_path, health_check_log_path
    )

    system_prompt = create_system_prompt(config.deployment.platform)
    prompt = create_fix_prompt(
        repo_path=repo_path,
        attempt=state["attempt"],
        max_attempts=state["max_attempts"],
        error_context=error_context,
        deploy_script_path=repo_path / ".sds" / "deploy.sh",
        health_check_script_path=repo_path / ".sds" / "health_check.sh",
    )

    response, messages = invoke_agent(
        state,
        agent,
        system_prompt,
        prompt,
        agent_name="Error Fixer",
        context_limit=context_limit,
        recorder=recorder,
    )
    state["messages"] = messages

    match = re.search(r"<summary>(.*?)</summary>", response, re.DOTALL)
    if match:
        summary_text = match.group(1)
        # Handle potential escaped characters
        summary_text = (
            summary_text.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\r", "\r")
            .strip()
        )
        log_file = repo_path / ".sds" / "logs" / f"fix_summary_{state['attempt']}.log"
        write_log_file(filesystem, log_file, summary_text)
        state["last_fix_summary"] = summary_text

    # End the current phase as we are about to retry (or fail if max attempts reached)
    # The graph logic handles max attempts check in should_fix edge.
    # If we are here, we are fixing.
    recorder.end_phase("needs_retry")

    state["attempt"] += 1
    return state
