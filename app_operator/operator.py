import signal
import sys
import time
from pathlib import Path
from typing import Optional, List, Any

from app_operator.agent_cli.base import CodingAgent
from app_operator.agent_cli.factory import create_agent_from_config
from app_operator.agents.deployer import DeploymentAgent
from app_operator.agents.app_monitor import AppMonitor


class AppOperator:
    """Manages automated deployment with coding-agent-assisted error fixing and extensible monitoring.

    This operator:
    1. Checks for or generates deployment scripts
    2. Attempts deployment and uses a coding agent to fix errors
    3. Monitors application health using a configurable set of MonitoringTasks
    """

    def __init__(self, repo_path: str, health_check_interval: int = 30,
                 agent: Optional[CodingAgent] = None):
        """Initialize the application operator.

        Args:
            repo_path: Path to the repository to deploy.
            health_check_interval: Seconds between health checks (default: 30).
            agent: Optional coding agent to use. If None, creates one from config.
        """
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval

        # Initialize agent if not provided
        if agent is None:
            try:
                self.agent = create_agent_from_config(str(self.repo_path))
            except RuntimeError as e:
                # Fallback or error if no default agent can be created
                raise RuntimeError(
                    f"Failed to initialize default coding agent: {e}")
        else:
            self.agent = agent

        self.sds_dir = self.repo_path / ".sds"
        self._shutdown_requested = False
        self._deployed = False

        # Initialize agents
        self.deployer = DeploymentAgent(self.repo_path, self.agent)
        self.monitor = AppMonitor(self.repo_path, self.agent)

        # Validate repository path
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not self.repo_path.is_dir():
            raise ValueError(
                f"Repository path is not a directory: {repo_path}")

    def run(self) -> int:
        """Main entry point for application operation.

        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        try:
            print(f"\n{'='*70}")
            print(f"  App Operator Mode")
            print(f"  Repository: {self.repo_path}")
            print(f"  Agent: {self.agent.__class__.__name__}")
            print(f"{'='*70}\n")

            # Step 1: Deploy with automatic error fixing (includes script
            # generation)
            if not self.deployer.run(
                    check_shutdown=lambda: self._shutdown_requested):
                print(
                    "\n✗ Failed to deploy application after multiple attempts",
                    file=sys.stderr)
                return 1

            self._deployed = True

            # Step 2: Monitor health and provide analysis
            self.monitor.run(
                interval=self.health_check_interval,
                check_shutdown=lambda: self._shutdown_requested
            )

            return 0

        except Exception as e:
            print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return 1
        finally:
            self._cleanup()

    def _handle_shutdown_signal(self, signum: int, frame):
        """Handle shutdown signals (SIGINT, SIGTERM).

        Args:
            signum: The signal number.
            frame: The current stack frame.
        """
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            print(
                f"\n\nReceived {signal_name} signal. Initiating graceful shutdown...")

    def _cleanup(self):
        """Shutdown the application and cleanup resources."""
        if not self._deployed:
            return

        print(f"\n{'='*70}")
        print(f"  Shutting Down Application")
        print(f"{'='*70}\n")

        print(f"Running deployment script stop command...")

        try:
            # Use deployer to stop
            result = self.deployer.run_deploy_command("stop", timeout=120)

            if result['success']:
                print(f"✓ Application stopped successfully")
            else:
                print(
                    f"⚠ Stop command exited with code {result['exit_code']}",
                    file=sys.stderr)
        except Exception as e:
            print(f"✗ Error during shutdown: {e}", file=sys.stderr)

        print(f"\n{'='*70}")
        print(f"  Shutdown Complete")
        print(f"{'='*70}\n")
