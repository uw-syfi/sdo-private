import signal
import threading
from pathlib import Path
from typing import Optional

from libs.agent_cli.base import CodingAgent
from libs.agent_cli.factory import create_agent_from_config
from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.cli_agent.agents.app_monitor import AppMonitor
from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.config import load_config, Config
from app_operator.ui import OperatorUI, NullOperatorUI
from app_operator.trajectory import TrajectoryRecorder


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
        ui: Optional[OperatorUI] = None,
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
            ui: Optional UI interface.
        """
        self.health_check_interval = health_check_interval
        self.health_check_max_count = health_check_max_count
        self.max_deployment_attempts = max_deployment_attempts
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.ui = ui or NullOperatorUI()

        # Convert to Path and resolve only for real filesystem
        # (InMemoryFilesystem doesn't need symlink resolution)
        if isinstance(self.filesystem, RealFilesystem):
            self.repo_path = Path(repo_path).resolve()
        else:
            # For InMemoryFilesystem, just use absolute path
            self.repo_path = Path(repo_path).absolute()

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
                raise AgentError(f"Failed to initialize default coding agent: {e}")
        else:
            self.agent = agent

        # Attach UI to agent if supported
        if hasattr(self.agent, "event_handler"):
            self.agent.event_handler = self.ui

        self._shutdown_requested = False
        self._deployed = False

        # Initialize trajectory recorder
        self.recorder = TrajectoryRecorder(self.repo_path)
        self.recorder.set_agent_name(self.agent.__class__.__name__)

        # Attach recorder to agent
        self.agent.recorder = self.recorder

        # Initialize agents
        self.analyzer = CodeAnalyzerAgent(
            self.repo_path,
            self.agent,
            self.filesystem,
            recorder=self.recorder,
            ui=self.ui,
        )
        self.deployer = DeploymentAgent(
            self.repo_path,
            self.agent,
            self.filesystem,
            self.config.deployment,
            self.config.operator,
            recorder=self.recorder,
            ui=self.ui,
        )
        self.monitor = AppMonitor(
            self.repo_path,
            self.agent,
            self.filesystem,
            recorder=self.recorder,
            ui=self.ui,
        )

    def _persist_deployment_config(self) -> None:
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

            self.ui.set_stage("Initializing")

            # Step 1: Code Analysis
            self.ui.set_stage("Code Analysis")
            self.analyzer.run()

            # Step 2: Deploy with automatic error fixing (includes script
            # generation)
            self.ui.set_stage("Deployment")
            if not self.deployer.run(
                max_attempts=self.max_deployment_attempts,
                check_shutdown=lambda: self._shutdown_requested,
            ):
                logger.error("Failed to deploy application after multiple attempts")
                return 1

            self._deployed = True

            # Step 3: Monitor health and provide analysis
            self.ui.set_stage("Monitoring")
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
            self.ui.close(
                status="completed" if self._deployed else "failed",
                exit_code=0 if self._deployed else 1,
            )
            self._cleanup()
            self.recorder.finalize("completed" if self._deployed else "failed")

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
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

    
    def _cleanup(self) -> None:
        """Shutdown the application and cleanup resources."""
        if not self._deployed:
            return

        # Check the feature flag from sds.toml
        use_dynamic_features = False
        try:
            import tomllib
            with open("sds.toml", "rb") as f:
                config_data = tomllib.load(f)
                # UPDATED: Look inside the 'operator' block
                use_dynamic_features = config_data.get('operator', {}).get('dynamic_observability_injection', False)
        except Exception as e:
            logger.debug(f"Could not read feature flag for shutdown: {e}")

        if use_dynamic_features:
            # FLAG IS TRUE (AI Remediation): Skip shutdown so the agent can inspect containers
            logger.info("Shutting Down Application (DISABLED FOR AI AGENT)")
            logger.info("Shutdown Skipped to allow AI Remediation")
        else:
            # FLAG IS FALSE (Original mode): Execute normal clean up for the rest of the team
            logger.info("Feature Flag OFF: Executing graceful shutdown...")
            try:
                # Use deployer to stop
                result = self.deployer.run_deploy_command("stop", timeout=120)

                if result["success"]:
                    logger.success("Application stopped successfully")
                else:
                    logger.warning(f"Stop command exited with code {result['exit_code']}")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")