from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.dspy_integration import DSPyConfig
    from libs.agent_cli.base import CodingAgent

from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    TrajectoryRecorderProtocol,
)
from app_operator.types import HealthVerdict
from app_operator.ui_protocol import NullOperatorUI, OperatorUI


def _parse_verdict(response: str) -> HealthVerdict | None:
    """Parse structured XML tags from an agent response.

    Returns a HealthVerdict if all required tags are found, else None.
    """
    verdict_match = re.search(
        r"<health_verdict>\s*(healthy|unhealthy)\s*</health_verdict>",
        response,
        re.IGNORECASE,
    )
    assessment_match = re.search(
        r"<health_assessment>(.*?)</health_assessment>",
        response,
        re.DOTALL,
    )
    diagnosis_match = re.search(
        r"<diagnosis>(.*?)</diagnosis>",
        response,
        re.DOTALL,
    )
    script_fixed_match = re.search(
        r"<script_fixed>\s*(true|false)\s*</script_fixed>",
        response,
        re.IGNORECASE,
    )

    if not verdict_match or not assessment_match:
        return None

    healthy = verdict_match.group(1).strip().lower() == "healthy"
    assessment = assessment_match.group(1).strip()
    diagnosis = diagnosis_match.group(1).strip() if diagnosis_match else ""
    script_was_fixed = script_fixed_match.group(1).strip().lower() == "true" if script_fixed_match else False

    # Healthy verdicts must have empty diagnosis
    if healthy:
        diagnosis = ""

    return HealthVerdict(
        healthy=healthy,
        assessment=assessment,
        diagnosis=diagnosis,
        script_was_fixed=script_was_fixed,
        raw_response=response,
    )


class AppHealthJudge:
    """Agent that critically assesses application health.

    Delegates health assessment to the coding agent, which runs the health
    check script, independently verifies app health, and iterates on the
    script until it is trustworthy.
    """

    def __init__(
        self,
        repo_path: Path,
        coding_agent: CodingAgent,
        health_check_script: Path,
        filesystem: FileSystemInterface | None = None,
        operator_config: OperatorConfig | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: DSPyConfig | None = None,
        ui: OperatorUI | None = None,
        deployment_config: DeploymentConfig | None = None,
    ):
        self.repo_path = repo_path
        self.agent = coding_agent
        self.health_check_script = health_check_script
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.operator_config = operator_config or OperatorConfig()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.deployment_config = deployment_config or DeploymentConfig()

    @classmethod
    def from_context(
        cls,
        ctx: AgentContext,
        deployment_config: DeploymentConfig | None = None,
    ) -> AppHealthJudge:
        """Create an AppHealthJudge from an AgentContext."""
        return cls(
            repo_path=ctx.repo_path,
            coding_agent=ctx.coding_agent,
            health_check_script=ctx.sds_dir / "health_check.sh",
            filesystem=ctx.filesystem,
            operator_config=ctx.operator_config,
            recorder=ctx.recorder,
            dspy_config=ctx.dspy_config,
            ui=ctx.ui,
            deployment_config=deployment_config,
        )

    def assess(self, max_retries: int = 2) -> HealthVerdict:
        """Run the agent-based health assessment.

        Args:
            max_retries: Maximum number of retries on parse failure.

        Returns:
            HealthVerdict with the agent's determination.
        """
        try:
            prompt = self._render_prompt()
        except Exception as e:
            logger.warning(f"Failed to render health assessment prompt: {e}")
            return HealthVerdict(
                healthy=False,
                assessment=f"Failed to render prompt: {e}",
                diagnosis="",
                script_was_fixed=False,
                raw_response="",
            )

        last_response = ""
        total_attempts = 1 + max_retries

        for attempt in range(total_attempts):
            try:
                response = self.agent.generate(
                    prompt,
                    cwd=str(self.repo_path),
                    timeout=self.operator_config.agent_timeout,
                )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.warning(f"Health assessment agent error: {e}")
                return HealthVerdict(
                    healthy=False,
                    assessment=f"Agent failed: {e}",
                    diagnosis="",
                    script_was_fixed=False,
                    raw_response="",
                )

            if response is None:
                response = ""
            last_response = response

            verdict = _parse_verdict(response)
            if verdict is not None:
                self._record_verdict(verdict)
                return verdict

            if attempt < total_attempts - 1:
                logger.warning(
                    f"Health assessment: failed to parse agent response "
                    f"(attempt {attempt + 1}/{total_attempts}), retrying..."
                )

        # All retries exhausted
        logger.warning(f"Health assessment: agent failed to produce structured output after {total_attempts} attempts")
        verdict = HealthVerdict(
            healthy=False,
            assessment=f"Agent failed to produce structured output after {total_attempts} attempts",
            diagnosis="",
            script_was_fixed=False,
            raw_response=last_response,
        )
        self._record_verdict(verdict)
        return verdict

    def _render_prompt(self) -> str:
        """Render the health assessment prompt template."""
        platform = self.deployment_config.platform
        return get_loader(self.dspy_config).render(
            "deployer/assess_health.jinja2",
            repo_path=self.repo_path,
            health_check_script=self.health_check_script,
            platform=platform,
            recorder=self.recorder,
        )

    def _record_verdict(self, verdict: HealthVerdict) -> None:
        """Record the verdict in the trajectory."""
        try:
            status = "healthy" if verdict.healthy else "unhealthy"
            self.recorder.add_assistant_message(f"Health assessment: {status}. {verdict.assessment}")
        except Exception as e:
            logger.debug(f"Failed to record health verdict: {e}")
