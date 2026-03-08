import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import (
    invoke_agent_structured,
    write_log_file,
)
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import PromptLoader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)


class HealthVerdictResponse(BaseModel):
    healthy: bool = Field(description="Whether the application is healthy")
    assessment: str = Field(description="What the script reported vs what was independently observed")
    diagnosis: str = Field(description="If unhealthy: symptoms and root causes. If healthy: empty string")
    script_was_fixed: bool = Field(description="Whether the health_check.sh script was modified")


def _run_health_agent(
    state: OperatorState,
    agent: Any,
    loader: PromptLoader,
    operator_config: Config,
    repo_path: Path,
    context_limit: int,
    recorder: TrajectoryRecorderProtocol,
) -> dict | None:
    """Invoke the health assessment agent and return the verdict as a dict."""
    health_check_script = repo_path / ".sds" / "health_check.sh"
    platform = operator_config.deployment.platform

    prompt = loader.render(
        "deployer/assess_health.jinja2",
        repo_path=repo_path,
        health_check_script=health_check_script,
        platform=platform,
        structured_output=True,
        recorder=recorder,
    )

    _response, _messages, structured = invoke_agent_structured(
        state,
        agent,
        "",
        prompt,
        agent_name="Health Judge",
        context_limit=context_limit,
        recorder=recorder,
    )

    if structured is not None:
        return structured.model_dump()

    logger.warning("Health agent did not return structured response, defaulting to unhealthy")
    return {
        "healthy": False,
        "assessment": "Agent did not return structured response",
        "diagnosis": "",
        "script_was_fixed": False,
    }


def _save_assessment_log(
    filesystem: FileSystemInterface,
    log_file: Path,
    verdict_dict: dict,
) -> None:
    """Write verdict to a log file matching AppMonitor format."""
    status = "healthy" if verdict_dict.get("healthy") else "unhealthy"
    content = (
        f"=== Health Assessment ===\n"
        f"Status: {status}\n"
        f"Script fixed: {verdict_dict.get('script_was_fixed', False)}\n\n"
        f"Assessment: {verdict_dict.get('assessment', '')}\n"
    )
    diagnosis = verdict_dict.get("diagnosis", "")
    if diagnosis:
        content += f"\nDiagnosis: {diagnosis}\n"

    write_log_file(filesystem, log_file, content)


def health_check(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    agent: Any,
    loader: PromptLoader,
    operator_config: Config,
    context_limit: int,
    check_shutdown: Callable[[], bool] | None,
    recorder: TrajectoryRecorderProtocol | None = None,
) -> OperatorState:
    recorder = recorder or NullTrajectoryRecorder()
    if check_shutdown and check_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    if not deploy_result.get("success"):
        state["health_result"] = None
        state["health_verdict"] = None
        return state

    logger.info("Running agent-based health assessment...")

    verdict_dict = _run_health_agent(
        state,
        agent,
        loader,
        operator_config,
        repo_path,
        context_limit,
        recorder,
    )
    state["health_verdict"] = verdict_dict
    # Keep health_result for backward compat with graph edges
    state["health_result"] = {"success": verdict_dict.get("healthy", False)} if verdict_dict else None

    if verdict_dict and verdict_dict.get("healthy"):
        recorder.end_phase("success")

    log_file = repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
    if verdict_dict:
        _save_assessment_log(filesystem, log_file, verdict_dict)

    return state


def monitor_health(
    state: OperatorState,
    repo_path: Path,
    filesystem: FileSystemInterface,
    agent: Any,
    loader: PromptLoader,
    operator_config: Config,
    context_limit: int,
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

    emit_progress("monitoring", cycle=state["monitor_count"])
    logger.info(f"Running agent-based health assessment (monitor cycle {state['monitor_count']})...")

    verdict_dict = _run_health_agent(
        state,
        agent,
        loader,
        operator_config,
        repo_path,
        context_limit,
        recorder,
    )
    state["health_verdict"] = verdict_dict
    state["health_result"] = {"success": verdict_dict.get("healthy", False)} if verdict_dict else None

    # Save assessment log
    log_file = (
        repo_path / ".sds" / "logs" / "monitor" / f"check_{state['monitor_count']}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    )
    if verdict_dict:
        _save_assessment_log(filesystem, log_file, verdict_dict)

    # End the monitoring phase
    recorder.end_phase("completed")

    return state
