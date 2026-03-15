"""Agent for health check and monitoring phases."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext

from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.pydantic_ai._base_agent import OperatorAgent
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import HealthVerdictResponse
from app_operator.pydantic_ai._soft_limit import soft_limit_history_processor, soft_limit_prepare_tools
from app_operator.script_runner import write_log_file
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.settings import ModelSettings

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class HealthAgent(OperatorAgent):
    """Agent for health check and monitoring phases."""

    # phase is not set at class level — it varies per call (DEPLOYMENT/MONITORING)
    # and is always passed explicitly to _run() in run_check().

    def __init__(
        self,
        model: str,
        model_settings: ModelSettings | None,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        super().__init__(deps, recorder, agent_name="Health Judge")
        self._agent: Agent[OperatorDeps, HealthVerdictResponse] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=HealthVerdictResponse,
            tools=tools,
            model_settings=model_settings,
            history_processors=[soft_limit_history_processor],
            prepare_tools=soft_limit_prepare_tools,
        )

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("health_judge_agent/system.jinja2")

    def run_check(
        self,
        *,
        phase: Phase,
        attempt: int | None = None,
        cycle: int | None = None,
    ) -> HealthVerdictResponse:
        """Run agent-based health assessment. Returns the verdict.

        Args:
            phase: The current operator phase (DEPLOYMENT or MONITORING).
            attempt: Deployment attempt number. Required when phase is DEPLOYMENT.
            cycle: Monitoring cycle number. Required when phase is MONITORING.

        Raises:
            ValueError: If the required parameter for the given phase is missing.
        """
        if phase == Phase.DEPLOYMENT and attempt is None:
            raise ValueError("attempt is required for DEPLOYMENT phase")
        if phase == Phase.MONITORING and cycle is None:
            raise ValueError("cycle is required for MONITORING phase")

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

        context = {"attempt": attempt} if attempt is not None else {"cycle": cycle}
        result = self._run(user_prompt, phase=phase, context=context)

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
            log_file = repo_path / ".sds" / "logs" / f"health_check_attempt_{attempt}.log"
        else:
            log_file = repo_path / ".sds" / "logs" / "monitor" / f"check_{cycle}_{time.strftime('%Y%m%d-%H%M%S')}.log"

        write_log_file(self.deps.filesystem, log_file, content)

        return verdict
