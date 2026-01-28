from pathlib import Path
from typing import Any

from app_operator.config import Config
from app_operator.prompts import PromptLoader
from app_operator.prompts.deployment_context import (
    analyze_repository,
    create_system_prompt,
)
from app_operator.prompts.deployer import create_generate_script_prompt
from app_operator.trajectory import Phase, record_phase_start
from app_operator.langgraph.state import OperatorState
from app_operator.langgraph.utils import invoke_agent


def generate_scripts(
    state: OperatorState,
    config: Config,
    repo_path: Path,
    loader: PromptLoader,
    agent: Any,
    context_limit: int,
) -> OperatorState:
    if state["scripts_done"]:
        return state

    record_phase_start(Phase.SCRIPT_GENERATION)

    system_prompt = create_system_prompt(config.deployment.platform)
    repo_context = analyze_repository(repo_path)

    deploy_prompt = create_generate_script_prompt(
        system_prompt=system_prompt,
        script_name="deploy.sh",
        repo_context=repo_context,
        target_dir=str(repo_path),
        platform=config.deployment.platform,
    )
    health_prompt = create_generate_script_prompt(
        system_prompt=system_prompt,
        script_name="health_check.sh",
        repo_context=repo_context,
        target_dir=str(repo_path),
        platform=config.deployment.platform,
    )

    _, _ = invoke_agent(
        state,
        agent,
        "",
        deploy_prompt,
        agent_name="Script Generator",
        context_limit=context_limit,
    )
    _, _ = invoke_agent(
        state,
        agent,
        "",
        health_prompt,
        agent_name="Script Generator",
        context_limit=context_limit,
    )

    state["scripts_done"] = True
    return state
