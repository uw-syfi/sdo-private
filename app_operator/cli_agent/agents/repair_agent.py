from __future__ import annotations

import re
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.types import CommandResult

from app_operator.constants import FIX_SUMMARY_FILENAME
from app_operator.exceptions import AgentError
from app_operator.logger import logger
from app_operator.prompts import (
    create_consolidation_prompt,
    create_fix_prompt,
    prepare_error_context,
)

FIX_SUMMARY_CONSOLIDATION_INTERVAL = 1
FIX_SUMMARY_MAX_LENGTH = 2000
FIX_SUMMARY_TRUNCATE_AT = 1900


def get_fix_summary_path(sds_dir: Path) -> Path:
    """Return the path to the consolidated fix summary file."""
    return sds_dir / FIX_SUMMARY_FILENAME


class RepairAgent:
    """Handles fix attempts: builds prompt, calls agent, extracts summary."""

    def __init__(self, ctx: AgentContext, deployment_config=None):
        self.ctx = ctx
        from app_operator.config import DeploymentConfig

        self.deployment_config = deployment_config or DeploymentConfig()

    def fix_with_agent(
        self,
        deploy_result: CommandResult,
        health_result: CommandResult | None,
        attempt: int,
        max_attempts: int,
        log_file_path: Path | None = None,
        health_check_log_path: Path | None = None,
    ) -> bool:
        """Use a coding agent to analyze errors and fix the scripts.

        Args:
            deploy_result: Deployment script result.
            health_result: Health check result (None if deployment failed before health check).
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
            error_context = prepare_error_context(deploy_result, health_result, log_file_path, health_check_log_path)
            prompt = create_fix_prompt(
                self.ctx.repo_path,
                attempt,
                max_attempts,
                error_context,
                deploy_script,
                health_check_script,
                platform=self.deployment_config.platform,
                dspy_config=self.ctx.dspy_config,
                recorder=self.ctx.recorder,
                fix_summary_consolidation=self.ctx.operator_config.phase.fix_summary_consolidation,
            )
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

            if self.ctx.operator_config.phase.fix_summary_consolidation:
                self._update_consolidated_summary(attempt, summary_text)

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

    def _update_consolidated_summary(self, current_attempt: int, current_summary: str) -> None:
        """Consolidate fix summaries into a markdown file using the agent."""
        if current_attempt % FIX_SUMMARY_CONSOLIDATION_INTERVAL != 0:
            return

        sds_dir = self.ctx.sds_dir
        summary_file = get_fix_summary_path(sds_dir)

        existing_content = ""
        if self.ctx.filesystem.exists(summary_file):
            existing_content = self.ctx.filesystem.read_text(summary_file)

        start_index = current_attempt - FIX_SUMMARY_CONSOLIDATION_INTERVAL + 1

        new_attempts_list = []
        for i in range(start_index, current_attempt + 1):
            if i == current_attempt:
                content = current_summary
            else:
                log_path = sds_dir / "logs" / f"fix_summary_{i}.log"
                if self.ctx.filesystem.exists(log_path):
                    content = self.ctx.filesystem.read_text(log_path)
                else:
                    content = "No summary available."

            new_attempts_list.append(f"## Attempt {i}\n{content}\n")

        new_attempts_text = "\n".join(new_attempts_list)

        prompt = create_consolidation_prompt(existing_content, new_attempts_text)

        logger.info("Consolidating fix summaries with agent...")
        try:
            consolidated_summary_raw = self.ctx.coding_agent.generate(
                prompt, cwd=str(self.ctx.repo_path), timeout=self.ctx.operator_config.agent_timeout, silent=True
            )

            match = re.search(r"<summary>(.*?)</summary>", consolidated_summary_raw, re.DOTALL)
            if match:
                consolidated_summary = match.group(1).strip()
            else:
                consolidated_summary = consolidated_summary_raw.strip()

            self.ctx.filesystem.write_text(summary_file, consolidated_summary)
            logger.info(f"Updated consolidated summary at {summary_file}")

        except (AgentError, OSError, RuntimeError) as e:
            logger.warning(f"Failed to consolidate summary: {e}")
            _max_fallback = 20_000
            if existing_content:
                if len(existing_content) > _max_fallback:
                    existing_content = existing_content[-_max_fallback:]
                fallback_content = existing_content + "\n\n" + new_attempts_text
            else:
                fallback_content = new_attempts_text
            self.ctx.filesystem.write_text(summary_file, fallback_content)
