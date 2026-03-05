import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app_operator.filesystem import FileSystemInterface
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import (
    invoke_agent,
    run_script,
    write_log_file,
)
from app_operator.logger import logger
from app_operator.prompts import PromptLoader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)


def health_check(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    check_shutdown: Callable[[], bool] | None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if check_shutdown and check_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    if not deploy_result.get("success"):
        state["health_result"] = None
        return state

    log_file = repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"

    logger.info("Running health check script...")
    logger.info(f"Logging output to: {log_file}")

    result = run_script(
        repo_path,
        filesystem,
        ".sds/health_check.sh",
        log_file_path=log_file,
        timeout=120,
        recorder=recorder,
    )
    state["health_result"] = result

    if result.get("success"):
        # If health check passes, deployment phase is successful
        recorder.end_phase("success")

    return state


def monitor_health(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    health_check_interval: int,
    check_shutdown: Callable[[], bool] | None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if check_shutdown and check_shutdown():
        return state

    # Start monitoring phase
    recorder.start_phase(Phase.MONITORING, {"cycle": state["monitor_count"]})

    interval = health_check_interval
    if interval > 0:
        time.sleep(interval)

    state["monitor_count"] += 1

    log_file = (
        repo_path / ".sds" / "logs" / "monitor" / f"check_{state['monitor_count']}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    )

    result = run_script(
        repo_path,
        filesystem,
        ".sds/health_check.sh",
        log_file_path=log_file,
        timeout=120,
        recorder=recorder,
    )
    state["health_result"] = result
    return state


def monitor_analyze(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    loader: PromptLoader,
    agent: Any,
    context_limit: int,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    # We are still in MONITORING phase initiated by monitor_health

    health_result = state.get("health_result") or {}
    context_parts = []
    context_parts.append(f"## Health Check #{state['monitor_count']}")
    context_parts.append(f"Exit Code: {health_result.get('exit_code')}")
    context_parts.append(f"Status: {'PASSED' if health_result.get('success') else 'FAILED'}")
    context_parts.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if health_result.get("stdout"):
        context_parts.append("\n### Output:")
        context_parts.append(health_result.get("stdout"))

    if health_result.get("stderr"):
        context_parts.append("\n### Errors:")
        context_parts.append(health_result.get("stderr"))

    context = "\n".join(context_parts)
    prompt = loader.render("monitor/analyze_health.jinja2", repo_path=repo_path, context=context)

    response, messages = invoke_agent(
        state,
        agent,
        "",
        prompt,
        agent_name="Health Monitor",
        context_limit=context_limit,
        recorder=recorder,
    )
    state["messages"] = messages

    log_file = repo_path / ".sds" / "logs" / "monitor" / f"analysis_{state['monitor_count']}.log"
    write_log_file(filesystem, log_file, response)

    # End the monitoring phase
    recorder.end_phase("completed")

    return state
