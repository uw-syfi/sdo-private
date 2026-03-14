"""Agent for script generation phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent

from app_operator.guardrails import ArtifactGuardrail
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import analyze_repository, create_generate_script_prompt
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai.agents.utils import _add_usage, _extract_usage
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class ScriptAgent:
    """Agent for script generation phase."""

    def __init__(
        self,
        model: str,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        self._agent: Agent[OperatorDeps, str] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=str,
            tools=tools,
        )
        self.deps = deps
        self.recorder = recorder

    def run(self) -> dict[str, int]:
        """Generate deploy.sh and health_check.sh. Returns token usage."""
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "requests": 0}

        emit_progress("script_generation")
        repo_path = self.deps.repo_path
        system_prompt = self.deps.loader.render(
            "script_generator/system.jinja2",
            platform=self.deps.config.deployment.platform,
        )
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
        result = self._agent.run_sync(
            deploy_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        _add_usage(usage, _extract_usage(result))
        self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator", result)

        for retry in range(deploy_guardrail.max_retries):
            missing = deploy_guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, deploy_guardrail.max_retries)
            result = self._agent.run_sync(
                deploy_guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
            )
            _add_usage(usage, _extract_usage(result))
            self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator (retry)", result)

        # Generate health_check.sh
        health_guardrail = ArtifactGuardrail([".sds/health_check.sh"])
        result = self._agent.run_sync(
            health_prompt,
            deps=self.deps,
            instructions=system_prompt,
        )
        _add_usage(usage, _extract_usage(result))
        self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator", result)

        for retry in range(health_guardrail.max_retries):
            missing = health_guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: {} missing (retry {}/{})", missing, retry + 1, health_guardrail.max_retries)
            result = self._agent.run_sync(
                health_guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
            )
            _add_usage(usage, _extract_usage(result))
            self.recorder.record_run(Phase.SCRIPT_GENERATION, "Script Generator (retry)", result)

        return usage
