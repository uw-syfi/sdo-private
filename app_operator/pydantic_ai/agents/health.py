"""Agent for health check and monitoring phases."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext, RunUsage
from pydantic_ai.usage import UsageLimits

from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import HealthVerdictResponse
from app_operator.script_runner import write_log_file
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class HealthAgent:
    """Agent for health check and monitoring phases."""

    def __init__(
        self,
        model: str,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        self._agent: Agent[OperatorDeps, HealthVerdictResponse] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=HealthVerdictResponse,
            tools=tools,
        )
        self.deps = deps
        self.recorder = recorder

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("health_judge_agent/system.jinja2")

    def run_check(
        self,
        *,
        phase: Phase,
        context: dict,
    ) -> tuple[HealthVerdictResponse, RunUsage]:
        """Run agent-based health assessment. Returns (verdict, token_usage)."""
        repo_path = self.deps.repo_path
        health_check_script = repo_path / ".sds" / "health_check.sh"
        platform = self.deps.config.deployment.platform

        deployment_progress_path = None
        if self.deps.config.operator.phase.fix_summary_consolidation:
            deployment_progress_path = repo_path / ".sds" / DEPLOYMENT_PROGRESS_FILENAME
        has_deployment_progress = deployment_progress_path is not None and deployment_progress_path.exists()

        user_prompt = self.deps.loader.render(
            "health_judge_agent/user.jinja2",
            repo_path=repo_path,
            health_check_script=health_check_script,
            platform=platform,
            structured_output=True,
            deployment_progress_path=deployment_progress_path,
            has_deployment_progress=has_deployment_progress,
        )

        result = self._agent.run_sync(
            user_prompt,
            deps=self.deps,
            usage_limits=UsageLimits(),
        )
        self.recorder.record_run(phase, "Health Judge", result, context=context)

        verdict = result.output
        status = "healthy" if verdict.healthy else "unhealthy"
        content = (
            f"=== Health Assessment ===\n"
            f"Status: {status}\n"
            f"Script fixed: {verdict.script_was_fixed}\n\n"
            f"Assessment: {verdict.assessment}\n"
        )
        if verdict.diagnosis:
            content += f"\nDiagnosis: {verdict.diagnosis}\n"

        if phase == Phase.DEPLOYMENT:
            attempt = context["attempt"]
            log_file = repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"
        else:
            cycle = context["cycle"]
            log_file = repo_path / ".sds" / "logs" / "monitor" / f"check_{cycle}_{time.strftime('%Y%m%d-%H%M%S')}.log"

        write_log_file(self.deps.filesystem, log_file, content)

        return verdict, result.usage()
