from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_operator.dspy_integration.config import DSPyConfig

from app_operator.ui_protocol import OperatorUI, NullOperatorUI
from libs.agent_cli.base import CodingAgent
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.trajectory import (
    Phase,
    TrajectoryRecorderProtocol,
    NullTrajectoryRecorder,
)

# Constants
DEFAULT_ANALYSIS_TIMEOUT_SECS = 600  # 10 minutes


class CodeAnalyzerAgent:
    """Agent responsible for analyzing the codebase before deployment."""

    def __init__(
        self,
        repo_path: Path,
        coding_agent: CodingAgent,
        filesystem: FileSystemInterface | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        dspy_config: "DSPyConfig" | None = None,
        ui: OperatorUI | None = None,
    ):
        """Initialize the code analyzer agent.

        Args:
            repo_path: Path to the repository to analyze.
            coding_agent: The coding agent to use for analysis.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            recorder: Trajectory recorder instance.
            dspy_config: Optional DSPy configuration for optimized prompts.
            ui: Optional UI interface.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.sds_dir = self.repo_path / ".sds"
        self.analysis_file = self.sds_dir / "code_analysis.md"
        self.issues_file = self.sds_dir / "deployment_issues.md"

    def _get_file_tree(self) -> str:
        """Generate a simple file tree of the repository."""
        try:
            # Get list of files, excluding hidden ones and common ignore patterns
            files = []
            for path in self.filesystem.rglob(self.repo_path, "*"):
                if self.filesystem.is_file(path) and not any(
                    p.startswith(".") for p in path.relative_to(self.repo_path).parts
                ):
                    files.append(str(path.relative_to(self.repo_path)))

            # Sort and limit to prevent context overflow
            files.sort()
            if len(files) > 100:
                files = files[:100] + ["... (truncated)"]

            return "\n".join(files)
        except Exception:
            return "Unable to generate file tree"

    def run(self) -> bool:
        """Run the code analysis.

        Returns:
            bool: True if analysis completed successfully or was already done.
        """
        # Check if analysis already exists
        if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(
            self.issues_file
        ):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return True

        logger.info("Starting Code Analysis Phase")

        with self.recorder.phase(Phase.EXPLORATION) as r:
            try:
                # Ensure .sds directory exists
                self.filesystem.mkdir(self.sds_dir, exist_ok=True)

                # Generate file tree for context
                file_tree = self._get_file_tree()

                # Create the prompt
                system_prompt = get_loader(self.dspy_config).render(
                    "code_analyzer/system.jinja2",
                    repo_path=self.repo_path,
                    agent_name=self.agent.__class__.__name__,  # Added for signature
                    recorder=self.recorder,
                )
                user_prompt = get_loader(self.dspy_config).render(
                    "code_analyzer/user.jinja2",
                    repo_path=self.repo_path,
                    file_tree=file_tree,  # Added for signature
                    recorder=self.recorder,
                )

                logger.info(
                    f"Consulting {self.agent.__class__.__name__} to analyze the codebase..."
                )

                start_time = time.time()

                # The agent is expected to use tools to explore and then write the files
                self.agent.generate(
                    f"{system_prompt}\n\n{user_prompt}",
                    cwd=str(self.repo_path),
                    timeout=DEFAULT_ANALYSIS_TIMEOUT_SECS,
                )

                duration = time.time() - start_time
                logger.info(f"Agent analysis took {duration / 60:.2f} minutes")

                # Verify files were created
                if self.filesystem.exists(
                    self.analysis_file
                ) and self.filesystem.exists(self.issues_file):
                    logger.success("Code analysis completed successfully")
                    r.add_assistant_message("Code analysis completed successfully")
                    return True
                else:
                    missing = []
                    if not self.filesystem.exists(self.analysis_file):
                        missing.append(str(self.analysis_file))
                    if not self.filesystem.exists(self.issues_file):
                        missing.append(str(self.issues_file))

                    error_msg = (
                        f"Agent failed to create analysis files: {', '.join(missing)}"
                    )
                    logger.error(error_msg)
                    r.set_phase_status("failed")
                    r.add_assistant_message(error_msg)
                    return False

            except Exception as e:
                logger.error(f"Code analysis failed: {e}")
                # The context manager catches exception and ends phase with "failed"
                r.add_assistant_message(f"Code analysis failed: {e}")
                r.set_phase_status("failed")
                return False
