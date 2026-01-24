import signal
import threading
from pathlib import Path
from typing import Optional

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.agents.deployer import DeploymentAgent
from app_operator.agents.app_monitor import AppMonitor
from app_operator.agents.code_analyzer import CodeAnalyzerAgent
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.config import load_config, Config
from tools.trajectory import init_trajectory, finalize_trajectory


class AppOperator:
    """Manages automated deployment with coding-agent-assisted error fixing and extensible monitoring.

    This operator:
    1. Checks for or generates deployment scripts
    2. Attempts deployment and uses a coding agent to fix errors
    3. Monitors application health using a configurable set of MonitoringTasks
    """

    def __init__(
        self,
        repo_path: str,
        health_check_interval: int = 30,
        health_check_max_count: Optional[int] = 5,
        max_deployment_attempts: int = 5,
        agent: Optional[CodingAgent] = None,
        filesystem: Optional[FileSystemInterface] = None,
        config: Optional[Config] = None,
    ):
        """Initialize the application operator.

        Args:
            repo_path: Path to the repository to deploy.
            health_check_interval: Seconds between health checks (default: 30).
            health_check_max_count: Maximum number of health checks (default: 5).
            max_deployment_attempts: Maximum deployment attempts (default: 5).
            agent: Optional coding agent to use. If None, creates one from config.
            filesystem: Optional filesystem abstraction. If None, uses RealFilesystem.
            config: Optional configuration object.
        """
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval
        self.health_check_max_count = health_check_max_count
        self.max_deployment_attempts = max_deployment_attempts
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()

        # Validate repository path
        if not self.filesystem.exists(self.repo_path):
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not self.filesystem.is_dir(self.repo_path):
            raise ValueError(f"Repository path is not a directory: {repo_path}")

        # Initialize config if not provided
        if config is None:
            self.config = load_config(str(self.repo_path))
        else:
            self.config = config

        self.sds_dir = self.repo_path / ".sds"

        # Persist deployment configuration
        self._persist_deployment_config()

        # Initialize agent if not provided
        if agent is None:
            try:
                self.agent = create_agent_from_config(
                    str(self.repo_path), config=self.config
                )
            except RuntimeError as e:
                # Fallback or error if no default agent can be created
                raise RuntimeError(f"Failed to initialize default coding agent: {e}")
        else:
            self.agent = agent

        self._shutdown_requested = False
        self._deployed = False

        # Initialize agents
        self.analyzer = CodeAnalyzerAgent(self.repo_path, self.agent, self.filesystem)
        self.deployer = DeploymentAgent(
            self.repo_path, self.agent, self.filesystem, self.config.deployment
        )
        self.monitor = AppMonitor(self.repo_path, self.agent, self.filesystem)

        # Initialize trajectory recorder
        self.trajectory = init_trajectory(self.repo_path)
        self.trajectory.set_agent_name(self.agent.__class__.__name__)

    def _persist_deployment_config(self):
        """Persist deployment preference to .sds/config.toml."""
        if not self.filesystem.exists(self.sds_dir):
            self.filesystem.mkdir(self.sds_dir)

        sds_config_path = self.sds_dir / "config.toml"

        # We only write if the file doesn't exist to avoid overwriting user edits,
        # ensuring we respect existing preferences if present (which would be loaded).
        # If not present, we create it to track the current preference.
        if not self.filesystem.exists(sds_config_path):
            logger.info(f"Creating deployment config at {sds_config_path}")
            config_content = (
                "[deployment]\n"
                f'platform = "{self.config.deployment.platform}"\n'
                f'target = "{self.config.deployment.target}"\n'
            )
            self.filesystem.write_text(sds_config_path, config_content)

    def run(self) -> int:
        """Main entry point for application operation.

        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        try:
            logger.info("Starting App Operator Mode")
            logger.info(f"Repository: {self.repo_path}")
            logger.info(f"Agent: {self.agent.__class__.__name__}")
            logger.info(f"Deployment Platform: {self.config.deployment.platform}")
            logger.info(f"Deployment Target: {self.config.deployment.target}")

            # Step 1: Code Analysis
            self.analyzer.run()

            # Step 2: Deploy with automatic error fixing (includes script
            # generation)
            if not self.deployer.run(
                max_attempts=self.max_deployment_attempts,
                check_shutdown=lambda: self._shutdown_requested,
            ):
                logger.error("Failed to deploy application after multiple attempts")
                return 1

            self._deployed = True

            # Step 3: Monitor health and provide analysis
            self.monitor.run(
                interval=self.health_check_interval,
                max_checks=self.health_check_max_count,
                check_shutdown=lambda: self._shutdown_requested,
            )

            return 0

        except KeyboardInterrupt:
            # Graceful shutdown initiated by signal handler
            logger.info("Shutting down due to interrupt...")
            return 1

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            import traceback

            traceback.print_exc()
            return 1
        finally:
            self._cleanup()
            finalize_trajectory("completed" if self._deployed else "failed")

    def _handle_shutdown_signal(self, signum: int, frame):
        """Handle shutdown signals (SIGINT, SIGTERM).

        Args:
            signum: The signal number.
            frame: The current stack frame.
        """
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info(
                f"Received {signal_name} signal. Initiating graceful shutdown..."
            )

            # Re-raise KeyboardInterrupt to interrupt blocking calls
            if signum == signal.SIGINT:
                raise KeyboardInterrupt()

    def _cleanup(self):
        """Shutdown the application and cleanup resources."""
        if not self._deployed:
            return

        logger.info("Shutting Down Application")

        logger.info("Running deployment script stop command...")

        try:
            # Use deployer to stop
            result = self.deployer.run_deploy_command("stop", timeout=120)

            if result["success"]:
                logger.success("Application stopped successfully")
            else:
                logger.warning(f"Stop command exited with code {result['exit_code']}")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        logger.info("Shutdown Complete")
