"""Agent for script generation phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext

from app_operator.guardrails import ArtifactGuardrail
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import analyze_repository, create_generate_script_prompt
from app_operator.pydantic_ai._base_agent import OperatorAgent
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.settings import ModelSettings

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class ScriptAgent(OperatorAgent):
    """Agent for script generation phase."""

    phase = Phase.SCRIPT_GENERATION

    def __init__(
        self,
        model: str,
        model_settings: ModelSettings | None,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        super().__init__(deps, recorder, agent_name="Script Generator")
        # output_type=str: return value is intentionally unused; real output is files written via tools.
        self._agent: Agent[OperatorDeps, str] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=str,
            tools=tools,
            model_settings=model_settings,
        )

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render(
                "script_generator/system.jinja2",
                platform=ctx.deps.config.deployment.platform,
            )

    def run(self) -> None:
        """Generate deploy.sh and health_check.sh."""
        emit_progress("script_generation")
        repo_path = self.deps.repo_path
        repo_context = analyze_repository(repo_path)

        deploy_prompt = create_generate_script_prompt(
            script_name="deploy.sh",
            repo_context=repo_context,
            target_dir=str(repo_path),
            platform=self.deps.config.deployment.platform,
        )
        health_prompt = create_generate_script_prompt(
            script_name="health_check.sh",
            repo_context=repo_context,
            target_dir=str(repo_path),
            platform=self.deps.config.deployment.platform,
        )

        # Generate deploy.sh
        deploy_guardrail = ArtifactGuardrail([".sds/deploy.sh"])
        result = self._run(deploy_prompt)

        for retry in range(deploy_guardrail.max_retries):
            missing = deploy_guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, deploy_guardrail.max_retries)
            result = self._run(
                deploy_guardrail.reminder(missing),
                message_history=result.all_messages(),
            )

        # Generate health_check.sh
        health_guardrail = ArtifactGuardrail([".sds/health_check.sh"])
        result = self._run(health_prompt)

        for retry in range(health_guardrail.max_retries):
            missing = health_guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, health_guardrail.max_retries)
            result = self._run(
                health_guardrail.reminder(missing),
                message_history=result.all_messages(),
            )
