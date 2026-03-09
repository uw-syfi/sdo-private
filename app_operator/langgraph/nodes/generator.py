from typing import Any

from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.guardrails import ArtifactGuardrail
from app_operator.langgraph.state import OperatorState
from app_operator.logger import logger as _logger
from app_operator.progress import emit_progress
from app_operator.prompts import (
    analyze_repository,
    create_generate_script_prompt,
    create_system_prompt,
)
from app_operator.trajectory import Phase

logger = _logger.bind(node="generator")


def generate_scripts(state: OperatorState, ctx: NodeContext, agent: Any) -> OperatorState:
    if state["scripts_done"]:
        return state

    emit_progress("script_generation")
    with ctx.recorder.phase(Phase.SCRIPT_GENERATION):
        system_prompt = create_system_prompt(ctx.config.deployment.platform)
        repo_context = analyze_repository(ctx.repo_path)

        deploy_prompt = create_generate_script_prompt(
            system_prompt=system_prompt,
            script_name="deploy.sh",
            repo_context=repo_context,
            target_dir=str(ctx.repo_path),
            platform=ctx.config.deployment.platform,
        )
        health_prompt = create_generate_script_prompt(
            system_prompt=system_prompt,
            script_name="health_check.sh",
            repo_context=repo_context,
            target_dir=str(ctx.repo_path),
            platform=ctx.config.deployment.platform,
        )

        deploy_guardrail = ArtifactGuardrail([".sds/deploy.sh"])
        health_guardrail = ArtifactGuardrail([".sds/health_check.sh"])

        result = ctx.invoke(state, agent, "", deploy_prompt, agent_name="Script Generator")
        for retry in range(deploy_guardrail.max_retries):
            missing = deploy_guardrail.missing(ctx.repo_path, ctx.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, deploy_guardrail.max_retries)
            result = ctx.invoke(state, agent, "", deploy_guardrail.reminder(missing), agent_name="Script Generator",
                                prior_messages=result.messages)

        result = ctx.invoke(state, agent, "", health_prompt, agent_name="Script Generator")
        for retry in range(health_guardrail.max_retries):
            missing = health_guardrail.missing(ctx.repo_path, ctx.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, health_guardrail.max_retries)
            result = ctx.invoke(state, agent, "", health_guardrail.reminder(missing), agent_name="Script Generator",
                                prior_messages=result.messages)

        state["scripts_done"] = True
        return state
