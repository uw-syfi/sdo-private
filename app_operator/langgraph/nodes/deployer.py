from typing import Any

from pydantic import BaseModel, Field

from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import run_script, write_log_file
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import (
    create_fix_prompt,
    prepare_error_context,
)
from app_operator.trajectory import Phase
from app_operator.types import HealthVerdict

logger = logger.bind(node="deployer")


class FixSummaryResponse(BaseModel):
    summary: str = Field(description="Brief summary of issues found and fixes applied")


def deploy_attempt(state: OperatorState, ctx: NodeContext) -> OperatorState:
    if ctx.should_shutdown():
        return state

    emit_progress("deployment", attempt=state["attempt"])
    ctx.recorder.start_phase(
        Phase.DEPLOYMENT,
        {"attempt": state["attempt"], "max_attempts": state["max_attempts"]},
    )

    log_file = ctx.repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"

    logger.info("Running deployment script...")
    logger.info(f"Logging output to: {log_file}")

    result = run_script(
        ctx.repo_path,
        ctx.filesystem,
        ".sds/deploy.sh start",
        log_file_path=log_file,
        timeout=ctx.config.operator.deploy_timeout,
        recorder=ctx.recorder,
    )
    state["deploy_result"] = result
    return state


def _health_verdict_from_dict(d: dict) -> HealthVerdict:
    return HealthVerdict(
        healthy=d.get("healthy", False),
        assessment=d.get("assessment", ""),
        diagnosis=d.get("diagnosis", ""),
        script_was_fixed=d.get("script_was_fixed", False),
        raw_response="",
    )


def fix_errors(state: OperatorState, ctx: NodeContext, fix_agent: Any) -> OperatorState:
    if ctx.should_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    health_verdict_dict = state.get("health_verdict")

    log_file_path = ctx.repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"
    health_check_log_path = None
    health_verdict = None
    if health_verdict_dict:
        health_check_log_path = ctx.repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
        health_verdict = _health_verdict_from_dict(health_verdict_dict)

    error_context = prepare_error_context(deploy_result, health_verdict, log_file_path, health_check_log_path)

    platform = ctx.config.deployment.platform
    deployment_progress_path = None
    if ctx.config.operator.phase.fix_summary_consolidation:
        deployment_progress_path = ctx.repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME

    prompt = create_fix_prompt(
        repo_path=ctx.repo_path,
        attempt=state["attempt"],
        max_attempts=state["max_attempts"],
        error_context=error_context,
        deploy_script_path=ctx.repo_path / ".sds" / "deploy.sh",
        health_check_script_path=ctx.repo_path / ".sds" / "health_check.sh",
        platform=platform,
        deployment_progress_path=deployment_progress_path,
        structured_output=True,
    )

    result = ctx.invoke(state, fix_agent, "", prompt, agent_name="Error Fixer", logger=logger)
    state["messages"] = result.messages

    if result.structured is not None:
        summary_text = result.structured.summary.strip()
    else:
        logger.warning("Fix agent did not return structured response, skipping summary")
        summary_text = None

    if summary_text:
        log_file = ctx.repo_path / ".sds" / "logs" / f"fix_summary_{state['attempt']}.log"
        write_log_file(ctx.filesystem, log_file, summary_text)

    ctx.recorder.end_phase("needs_retry")

    state["attempt"] += 1
    return state
