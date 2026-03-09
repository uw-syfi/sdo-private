import time
from pathlib import Path

from pydantic import BaseModel, Field

from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import write_log_file
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.trajectory import Phase

logger = logger.bind(node="monitor")


class HealthVerdictResponse(BaseModel):
    healthy: bool = Field(description="Whether the application is healthy")
    assessment: str = Field(description="What the script reported vs what was independently observed")
    diagnosis: str = Field(description="If unhealthy: symptoms and root causes. If healthy: empty string")
    script_was_fixed: bool = Field(description="Whether the health_check.sh script was modified")


def _run_health_agent(state: OperatorState, ctx: NodeContext) -> dict | None:
    """Invoke the health assessment agent and return the verdict as a dict."""
    health_check_script = ctx.repo_path / ".sds" / "health_check.sh"
    platform = ctx.config.deployment.platform

    prompt = ctx.loader.render(
        "deployer/assess_health.jinja2",
        repo_path=ctx.repo_path,
        health_check_script=health_check_script,
        platform=platform,
        structured_output=True,
        recorder=ctx.recorder,
    )

    result = ctx.invoke(state, ctx.health_agent, "", prompt, agent_name="Health Judge", logger=logger)

    if result.structured is not None:
        return result.structured.model_dump()

    logger.warning("Health agent did not return structured response, defaulting to unhealthy")
    return {
        "healthy": False,
        "assessment": "Agent did not return structured response",
        "diagnosis": "",
        "script_was_fixed": False,
    }


def _record_health_verdict(state: OperatorState, ctx: NodeContext, log_file: Path) -> dict | None:
    """Run health agent, update state, and save assessment log."""
    verdict_dict = _run_health_agent(state, ctx)
    state["health_verdict"] = verdict_dict
    if verdict_dict:
        _save_assessment_log(ctx.filesystem, log_file, verdict_dict)
    return verdict_dict


def _save_assessment_log(filesystem, log_file, verdict_dict: dict) -> None:
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


def health_check(state: OperatorState, ctx: NodeContext) -> OperatorState:
    if ctx.should_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    if not deploy_result.get("success"):
        state["health_verdict"] = None
        return state

    logger.info("Running agent-based health assessment...")

    log_file = ctx.repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
    verdict_dict = _record_health_verdict(state, ctx, log_file)

    if verdict_dict and verdict_dict.get("healthy"):
        ctx.recorder.end_phase("success")

    return state


def monitor_health(state: OperatorState, ctx: NodeContext) -> OperatorState:
    if ctx.should_shutdown():
        return state

    # Start monitoring phase
    ctx.recorder.start_phase(Phase.MONITORING, {"cycle": state["monitor_count"]})

    interval = ctx.health_check_interval
    if interval > 0:
        time.sleep(interval)

    state["monitor_count"] += 1

    emit_progress("monitoring", cycle=state["monitor_count"])
    logger.info(f"Running agent-based health assessment (monitor cycle {state['monitor_count']})...")

    log_file = (
        ctx.repo_path
        / ".sds"
        / "logs"
        / "monitor"
        / f"check_{state['monitor_count']}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    )
    _record_health_verdict(state, ctx, log_file)

    # End the monitoring phase
    ctx.recorder.end_phase("completed")

    return state
