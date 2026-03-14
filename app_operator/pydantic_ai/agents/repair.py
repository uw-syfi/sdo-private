"""Agent for error fixing phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext, RunUsage
from pydantic_ai.usage import UsageLimits

from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.prompts import create_fix_prompt, prepare_error_context
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import FixSummaryResponse, HealthVerdictResponse
from app_operator.script_runner import write_log_file
from app_operator.trajectory import Phase
from app_operator.types import HealthVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class RepairAgent:
    """Agent for error fixing phase."""

    def __init__(
        self,
        model: str,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
        max_attempts: int,
    ):
        self._agent: Agent[OperatorDeps, FixSummaryResponse] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=FixSummaryResponse,
            tools=tools,
        )
        self.deps = deps
        self.recorder = recorder
        self.max_attempts = max_attempts

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("repair_agent/system.jinja2")

    def run(self, deploy_result: dict, health_verdict: HealthVerdictResponse | None, attempt: int) -> RunUsage:
        """Run fix agent to diagnose and repair issues. Returns token usage."""
        repo_path = self.deps.repo_path
        log_file_path = repo_path / ".sds" / "logs" / f"deploy_attempt_{attempt}.log"
        health_check_log_path = None
        hv = None
        if health_verdict is not None:
            health_check_log_path = repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"
            hv = HealthVerdict(
                healthy=health_verdict.healthy,
                assessment=health_verdict.assessment,
                diagnosis=health_verdict.diagnosis,
                script_was_fixed=health_verdict.script_was_fixed,
                raw_response="",
            )

        error_context = prepare_error_context(deploy_result, hv, log_file_path, health_check_log_path)

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

        result = self._agent.run_sync(
            prompt,
            deps=self.deps,
            usage_limits=UsageLimits(),
        )
        self.recorder.record_run(Phase.DEPLOYMENT, "Error Fixer", result, context={"attempt": attempt})

        summary_text = result.output.summary.strip() if result.output else None
        if summary_text:
            log_file = repo_path / ".sds" / "logs" / f"fix_summary_{attempt}.log"
            write_log_file(self.deps.filesystem, log_file, summary_text)

        return result.usage()
