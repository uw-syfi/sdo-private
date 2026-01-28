import time
from pathlib import Path
from typing import Optional

from app_operator.cli_agent.backend.base import CodingAgent
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
        filesystem: Optional[FileSystemInterface] = None,
        recorder: Optional[TrajectoryRecorderProtocol] = None,
    ):
        """Initialize the code analyzer agent.

        Args:
            repo_path: Path to the repository to analyze.
            coding_agent: The coding agent to use for analysis.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            recorder: Trajectory recorder instance.
        """
        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.sds_dir = self.repo_path / ".sds"
        self.analysis_file = self.sds_dir / "code_analysis.md"
        self.issues_file = self.sds_dir / "deployment_issues.md"

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

                # Create the prompt
                system_prompt = get_loader().render("code_analyzer/system.jinja2")
                user_prompt = get_loader().render(
                    "code_analyzer/user.jinja2", repo_path=self.repo_path
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
