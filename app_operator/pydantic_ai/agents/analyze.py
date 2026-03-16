"""Agent for code analysis phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai import Agent

from pydantic_ai import RunContext  # noqa: TC002 — pydantic-ai evaluates @agent.instructions annotations at runtime

from app_operator.guardrails import ArtifactGuardrail
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.pydantic_ai._base_agent import OperatorAgent
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.settings import ModelSettings

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class AnalyzeAgent(OperatorAgent):
    """Agent for code analysis phase."""

    phase = Phase.EXPLORATION

    def __init__(
        self,
        model: str,
        model_settings: ModelSettings | None,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        super().__init__(deps, recorder, agent_name="Code Analyzer")
        # output_type=str: return value is intentionally unused; real output is files written via tools.
        self._agent: Agent[OperatorDeps, str] = self._build_agent(
            model,
            deps_type=OperatorDeps,
            output_type=str,
            tools=tools,
            model_settings=model_settings,
        )

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("code_analyzer/system.jinja2")

    def run(self) -> None:
        """Run code analysis phase."""
        repo_path = self.deps.repo_path
        sds_dir = repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if self.deps.filesystem.exists(analysis_file) and self.deps.filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return

        emit_progress("code_analysis")
        user_prompt = self.deps.loader.render("code_analyzer/user.jinja2", repo_path=repo_path)

        result = self._run(user_prompt)

        guardrail = ArtifactGuardrail([".sds/code_analysis.md", ".sds/deployment_issues.md"])
        for retry in range(guardrail.max_retries):
            missing = guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: missing {} (retry {}/{})", missing, retry + 1, guardrail.max_retries)
            result = self._run(
                guardrail.reminder(missing),
                message_history=result.all_messages(),
            )
        else:
            missing = guardrail.missing(repo_path, self.deps.filesystem)
            if missing:
                logger.warning("Guardrail: artifacts still missing after max retries: {}", missing)
