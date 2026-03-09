import re

from pydantic import BaseModel, Field

from app_operator.constants import FIX_SUMMARY_FILENAME
from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import (
    invoke_agent,
    run_script,
    write_log_file,
)
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import (
    create_consolidation_prompt,
    create_fix_prompt,
    prepare_error_context,
)
from app_operator.trajectory import Phase
from app_operator.types import HealthVerdict

logger = logger.bind(node="deployer")


class ConsolidatedSummaryResponse(BaseModel):
    summary: str = Field(description="The consolidated summary of all fix attempts")


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


def fix_errors(state: OperatorState, ctx: NodeContext) -> OperatorState:
    if ctx.should_shutdown():
        return state

    deploy_result = state.get("deploy_result") or {}
    health_verdict_dict = state.get("health_verdict")

    log_file_path = ctx.repo_path / ".sds" / "logs" / f"deploy_attempt_{state['attempt']}.log"
    health_check_log_path = None
    health_verdict = None
    if health_verdict_dict:
        health_check_log_path = ctx.repo_path / ".sds" / "logs" / f"health_check_attempt_{state['attempt']}.log"
        health_verdict = HealthVerdict(
            healthy=health_verdict_dict.get("healthy", False),
            assessment=health_verdict_dict.get("assessment", ""),
            diagnosis=health_verdict_dict.get("diagnosis", ""),
            script_was_fixed=health_verdict_dict.get("script_was_fixed", False),
            raw_response="",
        )

    error_context = prepare_error_context(deploy_result, health_verdict, log_file_path, health_check_log_path)

    platform = ctx.config.deployment.platform
    fix_summary_consolidation = ctx.config.operator.phase.fix_summary_consolidation

    prompt = create_fix_prompt(
        repo_path=ctx.repo_path,
        attempt=state["attempt"],
        max_attempts=state["max_attempts"],
        error_context=error_context,
        deploy_script_path=ctx.repo_path / ".sds" / "deploy.sh",
        health_check_script_path=ctx.repo_path / ".sds" / "health_check.sh",
        platform=platform,
        fix_summary_consolidation=fix_summary_consolidation,
    )

    result = invoke_agent(
        state,
        ctx.fix_agent,
        "",
        prompt,
        agent_name="Error Fixer",
        context_limit=ctx.context_limit,
        recorder=ctx.recorder,
        logger=logger,
    )
    state["messages"] = result.messages

    match = re.search(r"<summary>(.*?)</summary>", result.text, re.DOTALL)
    if match:
        summary_text = match.group(1)
        summary_text = summary_text.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").strip()
        log_file = ctx.repo_path / ".sds" / "logs" / f"fix_summary_{state['attempt']}.log"
        write_log_file(ctx.filesystem, log_file, summary_text)
        state["last_fix_summary"] = summary_text

        if fix_summary_consolidation and ctx.consolidation_agent is not None:
            _update_consolidated_summary(state, ctx, summary_text)

    ctx.recorder.end_phase("needs_retry")

    state["attempt"] += 1
    return state


def _update_consolidated_summary(
    state: OperatorState,
    ctx: NodeContext,
    current_summary: str,
) -> None:
    """Consolidate fix summaries into a markdown file using a structured agent."""
    sds_dir = ctx.repo_path / ".sds"
    summary_file = sds_dir / FIX_SUMMARY_FILENAME
    attempt = state["attempt"]

    existing_content = ""
    if ctx.filesystem.exists(summary_file):
        existing_content = ctx.filesystem.read_text(summary_file)

    new_attempts_text = f"## Attempt {attempt}\n{current_summary}\n"

    prompt = create_consolidation_prompt(existing_content, new_attempts_text)

    try:
        result = invoke_agent(
            state,
            ctx.consolidation_agent,
            "",
            prompt,
            agent_name="Summary Consolidator",
            context_limit=ctx.context_limit,
            recorder=ctx.recorder,
            logger=logger,
        )

        if result.structured is not None:
            consolidated_summary = result.structured.summary.strip()
        else:
            logger.warning("Consolidation agent did not return structured response, using raw response")
            consolidated_summary = result.text.strip()

        write_log_file(ctx.filesystem, summary_file, consolidated_summary)
        logger.info(f"Updated consolidated summary at {summary_file}")

    except (OSError, RuntimeError) as e:
        logger.warning(f"Failed to consolidate summary: {e}")
        _max_fallback = 20_000
        if existing_content:
            if len(existing_content) > _max_fallback:
                existing_content = existing_content[-_max_fallback:]
            fallback_content = existing_content + "\n\n" + new_attempts_text
        else:
            fallback_content = new_attempts_text
        write_log_file(ctx.filesystem, summary_file, fallback_content)
