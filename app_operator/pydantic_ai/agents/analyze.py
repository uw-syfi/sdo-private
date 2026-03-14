"""Agent for code analysis phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext, RunUsage
from pydantic_ai.usage import UsageLimits

from app_operator.guardrails import ArtifactGuardrail
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.trajectory import Phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from app_operator.pydantic_ai._trajectory import PydanticAITrajectoryRecorder


class AnalyzeAgent:
    """Agent for code analysis phase."""

    def __init__(
        self,
        model: str,
        tools: list[Callable],
        deps: OperatorDeps,
        recorder: PydanticAITrajectoryRecorder,
    ):
        # output_type=str: return value is intentionally unused; real output is files written via tools.
        self._agent: Agent[OperatorDeps, str] = Agent(
            model,
            deps_type=OperatorDeps,
            output_type=str,
            tools=tools,
        )
        self.deps = deps
        self.recorder = recorder

        @self._agent.instructions
        def system_prompt(ctx: RunContext[OperatorDeps]) -> str:
            return ctx.deps.loader.render("code_analyzer/system.jinja2")

    def run(self) -> RunUsage:
        """Run code analysis phase. Returns token usage."""
        total = RunUsage()

        repo_path = self.deps.repo_path
        sds_dir = repo_path / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        if self.deps.filesystem.exists(analysis_file) and self.deps.filesystem.exists(issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return total

        emit_progress("code_analysis")
        user_prompt = self.deps.loader.render("code_analyzer/user.jinja2", repo_path=repo_path)

        result = self._agent.run_sync(
            user_prompt,
            deps=self.deps,
            usage_limits=UsageLimits(),
        )
        total += result.usage()
        self.recorder.record_run(Phase.EXPLORATION, "Code Analyzer", result)

        guardrail = ArtifactGuardrail([".sds/code_analysis.md", ".sds/deployment_issues.md"])
        for retry in range(guardrail.max_retries):
            missing = guardrail.missing(repo_path, self.deps.filesystem)
            if not missing:
                break
            logger.warning("Guardrail: missing {} (retry {}/{})", missing, retry + 1, guardrail.max_retries)
            result = self._agent.run_sync(
                guardrail.reminder(missing),
                deps=self.deps,
                message_history=result.all_messages(),
                usage_limits=UsageLimits(),
            )
            total += result.usage()
            self.recorder.record_run(Phase.EXPLORATION, "Code Analyzer (retry)", result)
        else:
            missing = guardrail.missing(repo_path, self.deps.filesystem)
            if missing:
                logger.warning("Guardrail: artifacts still missing after max retries: {}", missing)

        return total
