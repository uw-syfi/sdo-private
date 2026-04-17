from __future__ import annotations

import re
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.types import CommandResult, HealthVerdict

from app_operator.agent_response_files import apply_agent_response_file_writes
from app_operator.config import DeploymentConfig
from app_operator.constants import DEPLOYMENT_PROGRESS_FILENAME
from app_operator.exceptions import AgentError
from app_operator.logger import logger
from app_operator.prompts import (
    create_fix_prompt,  # pyright: ignore[reportUnknownVariableType]
    create_fix_system_prompt,
    prepare_error_context,
)

FIX_SUMMARY_MAX_LENGTH = 2000
FIX_SUMMARY_TRUNCATE_AT = 1900


class RepairAgent:
    """Handles fix attempts: builds prompt, calls agent, extracts summary."""

    def __init__(self, ctx: AgentContext, deployment_config: DeploymentConfig | None = None):
        self.ctx = ctx
        self.deployment_config = deployment_config or DeploymentConfig()

    def fix_with_agent(
        self,
        deploy_result: CommandResult,
        health_verdict: HealthVerdict | None,
        attempt: int,
        max_attempts: int,
        log_file_path: Path | None = None,
        health_check_log_path: Path | None = None,
    ) -> bool:
        """Use a coding agent to analyze errors and fix the scripts.

        Args:
            deploy_result: Deployment script result.
            health_verdict: Health verdict (None if deployment failed before health check).
            attempt: Current attempt number.
            max_attempts: Maximum number of attempts.
            log_file_path: Path to the deployment log file.
            health_check_log_path: Path to the health check log file.

        Returns:
            bool: True if agent suggested a fix and applied it.
        """
        if attempt >= max_attempts:
            logger.error(f"Reached maximum attempts ({max_attempts}), giving up")
            return False

        sds_dir = self.ctx.sds_dir
        agent = self.ctx.coding_agent
        deploy_script = sds_dir / "deploy.sh"
        health_check_script = sds_dir / "health_check.sh"

        self.ctx.ui.set_stage("Fixing Deployment Issues", detail=f"Attempt {attempt}/{max_attempts}")
        logger.info(f"Asking {agent.__class__.__name__} to Fix Deployment Issues")

        try:
            error_context = prepare_error_context(deploy_result, health_verdict, log_file_path, health_check_log_path)
            deployment_progress_path = None
            if self.ctx.operator_config.phase.fix_summary_consolidation:
                deployment_progress_path = self.ctx.sds_dir / DEPLOYMENT_PROGRESS_FILENAME
            fix_system_prompt = create_fix_system_prompt()
            user_prompt = create_fix_prompt(
                self.ctx.repo_path,
                attempt,
                max_attempts,
                error_context,
                deploy_script,
                health_check_script,
                platform=self.deployment_config.platform,
                dspy_config=self.ctx.dspy_config,
                recorder=self.ctx.recorder,
                deployment_progress_path=deployment_progress_path,
            )
            prompt = fix_system_prompt + "\n\n" + user_prompt
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"Failed to prepare fix prompt: {e}")
            self.ctx.recorder.add_assistant_message(f"Failed to prepare fix prompt: {e}")
            return False

        try:
            logger.info(f"Consulting {agent.__class__.__name__} to analyze and fix the issue...")

            start_time = time.time()
            response = agent.generate(
                prompt,
                cwd=str(self.ctx.repo_path),
                timeout=self.ctx.operator_config.agent_fix_timeout,
            )
            duration = time.time() - start_time
            logger.info(f"Agent generation (fix) took {duration / 60:.2f} minutes")

            applied_paths = apply_agent_response_file_writes(response, self.ctx.repo_path, self.ctx.filesystem)
            if applied_paths:
                logger.info("Applied %d file updates from agent response", len(applied_paths))

            # Extract summary and save to log
            match = re.search(r"<summary>(.*?)</summary>", response, re.DOTALL)
            if match:
                summary_text = match.group(1).strip()
            else:
                logger.warning(f"Agent did not provide summary in expected format for attempt {attempt}")
                summary_text = (
                    "Agent attempted to fix deployment issues "
                    "(no structured summary provided).\n\n"
                    f"Full response:\n{response}"
                )
                if len(summary_text) > FIX_SUMMARY_MAX_LENGTH:
                    summary_text = summary_text[:FIX_SUMMARY_TRUNCATE_AT] + "...\n[Response truncated]"

            log_file = sds_dir / "logs" / f"fix_summary_{attempt}.log"
            self.ctx.filesystem.mkdir(log_file.parent, parents=True, exist_ok=True)
            self.ctx.filesystem.write_text(log_file, summary_text)
            logger.info(f"Saved fix summary to {log_file}")

            logger.info("Agent response received")
            logger.success("Agent has analyzed the issue and may have modified the scripts")
            logger.info("Proceeding to next deployment attempt...")

            return True

        except subprocess.TimeoutExpired:
            timeout_min = self.ctx.operator_config.agent_fix_timeout // 60
            logger.error(f"Agent fix timed out after {timeout_min} minutes")
            self.ctx.recorder.add_assistant_message(f"Agent fix timed out after {timeout_min} minutes")
            return False
        except AgentError as e:
            logger.error(f"Agent failed to provide fix: {e}")
            self.ctx.recorder.add_assistant_message(f"Failed to provide fix: {e}")
            return False
        except (OSError, RuntimeError, ValueError) as e:
            logger.error(f"Unexpected error while getting fix from agent: {e}", exc_info=True)
            self.ctx.recorder.add_assistant_message(f"Unexpected error during fix attempt: {e}")
            return False
