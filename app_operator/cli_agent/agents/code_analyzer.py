from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from app_operator.dspy_integration import DSPyConfig
    from libs.agent_cli.base import CodingAgent

from app_operator.cli_agent.agents.context import AgentContext
from app_operator.config import OperatorConfig
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.progress import emit_progress
from app_operator.prompts import get_loader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI


class CodeAnalyzerAgent:
    """Agent responsible for analyzing the codebase before deployment."""

    def __init__(
        self,
        repo_path: Path,
        coding_agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: DSPyConfig | None = None,
        ui: OperatorUI | None = None,
        operator_config: OperatorConfig | None = None,
        *,
        ctx: AgentContext | None = None,
    ):
        if ctx is not None:
            self._ctx = ctx
        else:
            self._ctx = AgentContext(
                repo_path=repo_path,
                coding_agent=coding_agent,
                filesystem=filesystem if filesystem is not None else RealFilesystem(),
                operator_config=operator_config or OperatorConfig(),
                recorder=recorder or NullTrajectoryRecorder(),
                dspy_config=dspy_config,
                ui=ui or NullOperatorUI(),
            )

        # Convenience aliases
        self.repo_path = self._ctx.repo_path
        self.agent = self._ctx.coding_agent
        self.filesystem = self._ctx.filesystem
        self.recorder = self._ctx.recorder
        self.dspy_config = self._ctx.dspy_config
        self.ui = self._ctx.ui
        self.operator_config = self._ctx.operator_config
        self.sds_dir = self._ctx.sds_dir
        self.analysis_file = self.sds_dir / "code_analysis.md"
        self.issues_file = self.sds_dir / "deployment_issues.md"

    def _get_file_tree(self) -> str:
        """Generate a simple file tree of the repository."""
        try:
            files = [
                str(path.relative_to(self.repo_path))
                for path in self.filesystem.rglob(self.repo_path, "*")
                if self.filesystem.is_file(path)
                and not any(p.startswith(".") for p in path.relative_to(self.repo_path).parts)
            ]

            files.sort()
            if len(files) > 100:
                files = files[:100] + ["... (truncated)"]

            return "\n".join(files)
        except OSError:
            return "Unable to generate file tree"

    def run(self) -> bool:
        """Run the code analysis.

        Returns:
            bool: True if analysis completed successfully or was already done.
        """
        if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(self.issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return True

        emit_progress("code_analysis")
        logger.info("Starting Code Analysis Phase")

        with self.recorder.phase(Phase.EXPLORATION) as r:
            try:
                self.filesystem.mkdir(self.sds_dir, exist_ok=True)

                file_tree = self._get_file_tree()

                system_prompt = get_loader(self.dspy_config).render(
                    "code_analyzer/system.jinja2",
                    repo_path=self.repo_path,
                    agent_name=self.agent.__class__.__name__,
                    recorder=self.recorder,
                )
                user_prompt = get_loader(self.dspy_config).render(
                    "code_analyzer/user.jinja2",
                    repo_path=self.repo_path,
                    file_tree=file_tree,
                    recorder=self.recorder,
                )

                logger.info(f"Consulting {self.agent.__class__.__name__} to analyze the codebase...")

                start_time = time.time()

                self.agent.generate(
                    f"{system_prompt}\n\n{user_prompt}",
                    cwd=str(self.repo_path),
                    timeout=self.operator_config.agent_timeout,
                )

                duration = time.time() - start_time
                logger.info(f"Agent analysis took {duration / 60:.2f} minutes")

                if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(self.issues_file):
                    logger.success("Code analysis completed successfully")
                    r.add_assistant_message("Code analysis completed successfully")
                    return True
                missing = []
                if not self.filesystem.exists(self.analysis_file):
                    missing.append(str(self.analysis_file))
                if not self.filesystem.exists(self.issues_file):
                    missing.append(str(self.issues_file))

                error_msg = f"Agent failed to create analysis files: {', '.join(missing)}"
                logger.error(error_msg)
                r.set_phase_status("failed")
                r.add_assistant_message(error_msg)
                return False

            except (AgentError, OSError, RuntimeError) as e:
                logger.error(f"Code analysis failed: {e}")
                r.add_assistant_message(f"Code analysis failed: {e}")
                r.set_phase_status("failed")
                return False
