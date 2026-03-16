"""Agent for error fixing phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai import Agent

from pydantic_ai import RunContext  # noqa: TC002 — pydantic-ai evaluates @agent.instructions annotations at runtime

from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.prompts import create_fix_prompt, prepare_error_context
from app_operator.pydantic_ai._base_agent import OperatorAgent
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import FixSummaryResponse, HealthVerdictResponse
from app_operator.script_runner import write_log_file
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.settings import ModelSettings

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder
    from app_operator.types import CommandResult


class RepairAgent(OperatorAgent):
    """Agent for error fixing phase."""

    phase = Phase.DEPLOYMENT

    def __init__(
        self,
        model: str,
        model_settings: ModelSettings | None,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
        max_attempts: int,
    ):
        super().__init__(deps, recorder, agent_name="Error Fixer")
        self._agent: Agent[OperatorDeps, FixSummaryResponse] = self._build_agent(
            model,
            deps_type=OperatorDeps,
            output_type=FixSummaryResponse,
            tools=tools,
            model_settings=model_settings,
        )
        self.max_attempts = max_attempts

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("repair_agent/system.jinja2")

    def run(self, deploy_result: CommandResult, health_verdict: HealthVerdictResponse | None, attempt: int) -> None:
        """Run fix agent to diagnose and repair issues."""
        repo_path = self.deps.repo_path
        log_file_path = repo_path / ".sds" / "logs" / f"deploy_attempt_{attempt}.log"
        health_check_log_path = None
        if health_verdict is not None:
            health_check_log_path = repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"

        error_context = prepare_error_context(deploy_result, health_verdict, log_file_path, health_check_log_path)

        platform = self.deps.config.deployment.platform
        deployment_progress_path = None
        if self.deps.config.operator.phase.fix_summary_consolidation:
            deployment_progress_path = repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME

        prompt = create_fix_prompt(
            repo_path=repo_path,
            attempt=attempt,
            max_attempts=self.max_attempts,
            error_context=error_context,
            deploy_script_path=repo_path / ".sds" / "deploy.sh",
            health_check_script_path=repo_path / ".sds" / "health_check.sh",
            platform=platform,
            deployment_progress_path=deployment_progress_path,
            structured_output=True,
        )

        result = self._run(prompt, context={"attempt": attempt})

        summary_text = result.output.summary.strip() or None
        if summary_text:
            log_file = repo_path / ".sds" / "logs" / f"fix_summary_{attempt}.log"
            write_log_file(self.deps.filesystem, log_file, summary_text)
