from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from app_operator.cli_agent.agents.context import AgentContext
    from app_operator.dspy_integration import DSPyConfig
    from libs.agent_cli.base import CodingAgent

from app_operator.config import OperatorConfig
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.prompts import get_loader
from app_operator.trajectory import (
    NullTrajectoryRecorder,
    Phase,
    TrajectoryRecorderProtocol,
)
from app_operator.ui_protocol import NullOperatorUI, OperatorUI
from libs.agent_cli.utils import generate_and_write_files


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
        ctx: AgentContext | None = None,
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
        if ctx is not None:
            filesystem = filesystem if filesystem is not None else ctx.filesystem
            recorder = recorder if recorder is not None else ctx.recorder
            dspy_config = dspy_config if dspy_config is not None else ctx.dspy_config
            ui = ui if ui is not None else ctx.ui
            operator_config = operator_config if operator_config is not None else ctx.operator_config

        self.repo_path = repo_path
        self.agent = coding_agent
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.recorder = recorder or NullTrajectoryRecorder()
        self.dspy_config = dspy_config
        self.ui = ui or NullOperatorUI()
        self.operator_config = operator_config or OperatorConfig()
        self.sds_dir = self.repo_path / ".sds"
        self.analysis_file = self.sds_dir / "code_analysis.md"
        self.issues_file = self.sds_dir / "deployment_issues.md"

    def _get_file_tree(self) -> str:
        """Generate a simple file tree of the repository."""
        try:
            # Get list of files, excluding hidden ones and common ignore patterns
            files = [
                str(path.relative_to(self.repo_path))
                for path in self.filesystem.rglob(self.repo_path, "*")
                if self.filesystem.is_file(path)
                and not any(p.startswith(".") for p in path.relative_to(self.repo_path).parts)
            ]

            # Sort and limit to prevent context overflow
            files.sort()
            if len(files) > 100:
                files = files[:100] + ["... (truncated)"]

            return "\n".join(files)
        except OSError:
            return "Unable to generate file tree"

    def _gather_repo_content(self) -> str:
        """Read key repository files and return their contents for LLM context."""
        parts = []
        _MAX_FILE_BYTES = 8_000

        def _read(path: object) -> str:
            from pathlib import Path as _Path

            p = _Path(str(path))
            try:
                if p.exists():
                    text = p.read_text(errors="replace")
                    if len(text) > _MAX_FILE_BYTES:
                        text = text[:_MAX_FILE_BYTES] + "\n... (truncated)"
                    return text
            except OSError:
                pass
            return ""

        # Compose files — most important for understanding services
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            content = _read(self.repo_path / name)
            if content:
                parts.append(f"--- {name} ---\n{content}")
                break

        # Top-level Dockerfile
        content = _read(self.repo_path / "Dockerfile")
        if content:
            parts.append(f"--- Dockerfile ---\n{content}")

        # README
        for name in ("README.md", "README.rst", "README"):
            content = _read(self.repo_path / name)
            if content:
                parts.append(f"--- {name} ---\n{content}")
                break

        return "\n\n".join(parts)

    def run(self) -> bool:
        """Run the code analysis.

        Returns:
            bool: True if analysis completed successfully or was already done.
        """
        # Check if analysis already exists
        if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(self.issues_file):
            logger.info("Code analysis files already exist. Skipping analysis.")
            return True

        logger.info("Starting Code Analysis Phase")

        with self.recorder.phase(Phase.EXPLORATION) as r:
            try:
                # Ensure .sds directory exists
                self.filesystem.mkdir(self.sds_dir, exist_ok=True)

                # Generate file tree and gather key file contents for context
                file_tree = self._get_file_tree()
                repo_content = self._gather_repo_content()

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
                    file_tree=file_tree,
                    repo_content=repo_content,
                    recorder=self.recorder,
                )

                logger.info(f"Consulting {self.agent.__class__.__name__} to analyze the codebase...")

                start_time = time.time()

                # The agent is expected to use tools to explore and then write the files
                raw_response = self.agent.generate(
                    f"{system_prompt}\n\n{user_prompt}",
                    cwd=str(self.repo_path),
                    timeout=self.operator_config.agent_timeout,
                )

                duration = time.time() - start_time
                logger.info(f"Agent analysis took {duration / 60:.2f} minutes")

                # Verify files were created
                if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(self.issues_file):
                    logger.success("Code analysis completed successfully")
                    r.add_assistant_message("Code analysis completed successfully")
                    return True

                # Fallback: some providers return FILE blocks but do not write files.
                generate_and_write_files(raw_response, user_prompt, self.repo_path, "[CodeAnalyzer]")
                if self.filesystem.exists(self.analysis_file) and self.filesystem.exists(self.issues_file):
                    logger.success("Code analysis completed successfully via response parsing fallback")
                    r.add_assistant_message("Code analysis completed successfully via response parsing fallback")
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
                # The context manager catches exception and ends phase with "failed"
                r.add_assistant_message(f"Code analysis failed: {e}")
                r.set_phase_status("failed")
                return False
