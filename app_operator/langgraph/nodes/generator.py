from typing import Any

from app_operator.langgraph.context import NodeContext
from app_operator.langgraph.state import OperatorState
from app_operator.progress import emit_progress
from app_operator.prompts import (
    analyze_repository,
    create_generate_script_prompt,
    create_system_prompt,
)
from app_operator.trajectory import Phase


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

        ctx.invoke(state, agent, "", deploy_prompt, agent_name="Script Generator")
        ctx.invoke(state, agent, "", health_prompt, agent_name="Script Generator")

        state["scripts_done"] = True
        return state
