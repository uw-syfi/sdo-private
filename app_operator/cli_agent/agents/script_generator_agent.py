from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_operator.dspy_integration import DSPyConfig
    from libs.agent_cli.base import CodingAgent

from app_operator.cli_agent.agents.context import AgentContext
from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.config import DeploymentConfig, OperatorConfig
from app_operator.exceptions import AgentError, DeploymentError, FileSystemError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import (
    analyze_repository,
    create_generate_script_prompt,
    create_system_prompt,
)
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)


class ScriptGeneratorAgent:
    """Generates deploy.sh and health_check.sh scripts using a coding agent."""

    def __init__(self, ctx: AgentContext, deployment_config: DeploymentConfig | None = None):
        self.ctx = ctx
        self.deployment_config = deployment_config or DeploymentConfig()

    def generate_scripts(self) -> tuple[bool, str]:
        """Generate deploy.sh and health_check.sh.

        Returns:
            Tuple of (success: bool, message: str).
        """
        target_path = self.ctx.repo_path
        filesystem = self.ctx.filesystem
        sds_dir = self.ctx.sds_dir

        if not filesystem.exists(target_path):
            return False, f"Target directory does not exist: {target_path}"

        if not filesystem.is_dir(target_path):
            return False, f"Target path is not a directory: {target_path}"

        try:
            filesystem.mkdir(sds_dir, exist_ok=True)
        except OSError as e:
            raise FileSystemError(f"Failed to create .sds directory {sds_dir}: {e}") from e

        abs_target_dir = str(target_path)

        with self.ctx.recorder.phase(Phase.SCRIPT_GENERATION) as r:
            try:
                system_prompt = create_system_prompt(self.deployment_config.platform)
                repo_context = analyze_repository(target_path, filesystem=filesystem)

                deploy_success, deploy_msg = self._generate_script(
                    system_prompt,
                    repo_context,
                    abs_target_dir,
                    "deploy.sh",
                    r,
                )

                if not deploy_success:
                    r.set_phase_status("failed")
                    return False, f"Failed to generate deploy.sh: {deploy_msg}"

                health_check_success, health_check_msg = self._generate_script(
                    system_prompt,
                    repo_context,
                    abs_target_dir,
                    "health_check.sh",
                    r,
                )

                deploy_script_path = sds_dir / "deploy.sh"
                health_check_script_path = sds_dir / "health_check.sh"

                try:
                    if filesystem.exists(deploy_script_path):
                        filesystem.chmod(deploy_script_path, 0o755)

                    if filesystem.exists(health_check_script_path):
                        filesystem.chmod(health_check_script_path, 0o755)
                except OSError as e:
                    raise FileSystemError(f"Failed to set script permissions: {e}") from e

                return True, f"Successfully generated scripts in {sds_dir}"

            except (AgentError, DeploymentError, FileSystemError) as e:
                r.set_phase_status("failed")
                return False, f"Failed to generate scripts: {e}"
            except (OSError, RuntimeError, ValueError) as e:
                logger.error("Unexpected error during script generation: %s", e, exc_info=True)
                r.set_phase_status("failed")
                return False, f"Unexpected error during script generation: {e}"

    def _generate_script(
        self,
        system_prompt: str,
        repo_context: str,
        target_dir: str,
        script_name: str,
        recorder: TrajectoryRecorderProtocol,
    ) -> tuple[bool, str]:
        """Generate a single script file."""
        platform = self.deployment_config.platform
        user_prompt = create_generate_script_prompt(
            script_name=script_name,
            repo_context=repo_context,
            target_dir=target_dir,
            platform=platform,
            dspy_config=self.ctx.dspy_config,
            recorder=recorder,
        )
        full_prompt = system_prompt + "\n\n" + user_prompt

        try:
            start_time = time.time()
            self.ctx.coding_agent.generate(
                full_prompt,
                cwd=target_dir,
                timeout=self.ctx.operator_config.agent_timeout,
            )

            duration = time.time() - start_time
            logger.info(f"Agent generation took {duration / 60:.2f} minutes")

            script_path = Path(target_dir) / ".sds" / script_name
            if self.ctx.filesystem.exists(script_path):
                return True, f"Successfully generated {script_name}"
            return False, f"Agent failed to create .sds/{script_name}"

        except subprocess.TimeoutExpired:
            timeout = self.ctx.operator_config.agent_timeout // 60
            recorder.add_assistant_message(f"Script generation timed out after {timeout} minutes")
            return (
                False,
                f"agent command timed out after {timeout} minutes",
            )
        except AgentError as e:
            recorder.add_assistant_message(f"Script generation failed: {e}")
            return False, str(e)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("Unexpected error in _generate_script: %s", e)
            recorder.add_assistant_message(f"Script generation failed: {e}")
            return False, str(e)


def generate_scripts(
    target_dir: str,
    agent: CodingAgent | None = None,
    filesystem: FileSystemInterface | None = None,
    deployment_config: DeploymentConfig | None = None,
    operator_config: OperatorConfig | None = None,
    recorder: TrajectoryRecorderProtocol | None = None,
    dspy_config: DSPyConfig | None = None,
) -> tuple[bool, str]:
    """Generate deploy.sh and health_check.sh scripts using a coding agent.

    Backward-compatible free function that delegates to ScriptGeneratorAgent.

    Args:
        target_dir: The directory path where scripts should be generated.
        agent: Optional CodingAgent instance. If None, creates one from config.
        filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
        deployment_config: Optional deployment configuration. If None, uses default.
        operator_config: Optional operator configuration for timeouts. If None, uses default.
        recorder: Optional trajectory recorder.
        dspy_config: Optional DSPy configuration for optimized prompts.

    Returns:
        Tuple of (success: bool, message: str).
    """
    if filesystem is None:
        filesystem = RealFilesystem()

    if deployment_config is None:
        deployment_config = DeploymentConfig()

    if operator_config is None:
        operator_config = OperatorConfig()

    recorder = recorder or NullTrajectoryRecorder()

    target_path = Path(target_dir).resolve()

    if not filesystem.exists(target_path):
        return False, f"Target directory does not exist: {target_dir}"

    if not filesystem.is_dir(target_path):
        return False, f"Target path is not a directory: {target_dir}"

    if agent is None:
        try:
            agent = create_agent_from_config(str(target_path))
        except (RuntimeError, AgentError) as e:
            return False, str(e)

    agent.recorder = recorder

    ctx = AgentContext(
        repo_path=target_path,
        coding_agent=agent,
        filesystem=filesystem,
        operator_config=operator_config,
        recorder=recorder,
        dspy_config=dspy_config,
    )

    gen = ScriptGeneratorAgent(ctx, deployment_config=deployment_config)
    return gen.generate_scripts()
