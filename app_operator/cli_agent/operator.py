import json
import signal
import subprocess
import threading
import time
from pathlib import Path

from app_operator.cli_agent.agents.app_monitor import AppMonitor
from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
from app_operator.cli_agent.agents.context import AgentContext
from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.config import Config, load_config
from app_operator.exceptions import AgentError, SdsOperatorError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.operator_base import OperatorBase
from app_operator.progress import emit_progress
from app_operator.prompts import get_loader
from app_operator.trajectory import TrajectoryRecorder
from app_operator.ui_protocol import NullOperatorUI, OperatorUI
from libs.agent_cli.base import CodingAgent


class AppOperator(OperatorBase):
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
        health_check_max_count: int | None = 5,
        max_deployment_attempts: int = 5,
        agent: CodingAgent | None = None,
        filesystem: FileSystemInterface | None = None,
        config: Config | None = None,
        ui: OperatorUI | None = None,
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

        # Use absolute() which works for both real and in-memory filesystems
        # without making OS syscalls like resolve() does
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
                self.agent = create_agent_from_config(str(self.repo_path), config=self.config)
            except RuntimeError as e:
                # Fallback or error if no default agent can be created
                raise AgentError(f"Failed to initialize default coding agent: {e}") from e
        else:
            self.agent = agent

        # Inject MCP server config into the experiment's agent settings when
        # dynamic observability is enabled. Providers that don't support this
        # will raise NotImplementedError, which we silently skip.
        if self.config.operator.prometheus_integration:
            sds_root = Path(__file__).resolve().parent.parent.parent
            try:
                self.agent.inject_mcp_server(self.repo_path, sds_root)
            except NotImplementedError:
                pass

        # Attach UI to agent if supported
        if hasattr(self.agent, "event_handler"):
            self.agent.event_handler = self.ui

        self._shutdown_requested = False
        self._deployed = False

        # Initialize trajectory recorder
        self.recorder = TrajectoryRecorder(self.repo_path)
        self.recorder.set_agent_name(self.agent.__class__.__name__)

        # Pick up fault injection metadata if present
        fault_meta_path = self.sds_dir / "fault_injection.json"
        if self.filesystem.exists(fault_meta_path):
            try:
                fault_meta = json.loads(self.filesystem.read_text(fault_meta_path))
                self.recorder.record_fault_injection(fault_meta)
                logger.info(f"Loaded fault injection metadata: {fault_meta.get('num_faults_injected', 0)} fault(s)")
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load fault injection metadata: {e}")

        # Attach recorder to agent
        self.agent.recorder = self.recorder

        # Construct shared context for all agents
        self._ctx = AgentContext(
            repo_path=self.repo_path,
            coding_agent=self.agent,
            filesystem=self.filesystem,
            operator_config=self.config.operator,
            recorder=self.recorder,
            dspy_config=self.config.dspy,
            ui=self.ui,
        )

        # Initialize agents with shared context
        self.analyzer = CodeAnalyzerAgent(
            self.repo_path,
            self.agent,
            ctx=self._ctx,
        )
        self.deployer = DeploymentAgent(
            self.repo_path,
            self.agent,
            deployment_config=self.config.deployment,
            ctx=self._ctx,
        )
        self.monitor = AppMonitor(
            self.repo_path,
            self.agent,
            ctx=self._ctx,
        )

    def run(self) -> int:
        """Main entry point for application operation.

        Returns:
            int: Exit code (0 for success, 1 for failure).
        """
        # Setup signal handlers for graceful shutdown
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        run_succeeded = False
        try:
            logger.info("Starting App Operator Mode")
            logger.info(f"Repository: {self.repo_path}")
            logger.info(f"Agent: {self.agent.__class__.__name__}")
            logger.info(f"Deployment Platform: {self.config.deployment.platform}")
            logger.info(f"Deployment Target: {self.config.deployment.target}")

            self.ui.set_stage("Initializing")

            # Step 1: Code Analysis (conditional)
            if self.config.operator.phase.code_analysis:
                self.ui.set_stage("Code Analysis")
                self.analyzer.run()
            else:
                logger.info("Code analysis disabled by configuration, skipping")

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
            if self.config.operator.phase.health_monitoring:
                self.ui.set_stage("Monitoring")
                self.monitor.run(
                    interval=self.health_check_interval,
                    max_checks=self.health_check_max_count,
                    check_shutdown=lambda: self._shutdown_requested,
                )
            else:
                logger.info("Health monitoring disabled by configuration, skipping")

            run_succeeded = True
            if self.config.operator.prometheus_integration:
                self._trigger_ai_remediation(max_retries=5)
            return 0

        except KeyboardInterrupt:
            # Graceful shutdown initiated by signal handler
            logger.info("Shutting down due to interrupt...")
            return 1

        except (SdsOperatorError, AgentError, OSError, RuntimeError, ValueError) as e:
            # Top-level catch to prevent uncaught exception — specific types are too numerous
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return 1
        finally:
            self.ui.close(
                status="completed" if run_succeeded else "failed",
                exit_code=0 if run_succeeded else 1,
            )
            self._cleanup()
            self.recorder.finalize("completed" if run_succeeded else "failed")

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM).

        Sets the ``_shutdown_requested`` flag so that running loops exit
        gracefully.  We intentionally do **not** raise ``KeyboardInterrupt``
        from the signal handler because doing so is dangerous in
        multi-threaded code (it can land in an arbitrary frame).

        Args:
            signum: The signal number.
            frame: The current stack frame.
        """
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info(f"Received {signal_name} signal. Initiating graceful shutdown...")

    def _cleanup(self) -> None:
        """Shutdown the application and cleanup resources."""
        if not self._deployed:
            return

        emit_progress("finishing")
        logger.info("Shutting Down Application")
        logger.info("Running deployment script stop command...")

        try:
            result = self.deployer.run_deploy_command("stop", timeout=120)
            if result["success"]:
                logger.success("Application stopped successfully")
            else:
                logger.warning(f"Stop command exited with code {result['exit_code']}")
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"Error during shutdown: {e}")

        logger.info("Shutdown Complete")

    def _trigger_ai_remediation(self, max_retries: int) -> bool:
        """Run the Gemini SRE agent in a loop until the system is healthy
        or we run out of retries."""
        try:
            playbook_content = get_loader().render("sre/startup_playbook.jinja2")
        except (OSError, RuntimeError) as e:
            logger.warning(f"⚠️  Failed to load SRE playbook: {e}")
            return False

        print("\n" + "=" * 50)
        print(f"🤖 [SDS Operator] STARTING AUTO-HEALING LOOP (Max Retries: {max_retries})")
        print("=" * 50)

        for attempt in range(1, max_retries + 1):
            print(f"\n🔄 [Attempt {attempt}/{max_retries}] Summoning SRE Agent...")

            try:
                process = subprocess.Popen(
                    ["gemini", "-y", playbook_content],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )

                full_output = ""
                for line in process.stdout or []:
                    print(line, end="")
                    full_output += line

                process.wait()

                if "SYSTEM HEALTHY" in full_output:
                    print(f"\n✅ [SDS Operator] Success! System healed on attempt {attempt}.")
                    return True

                print("\n⚠️ [SDS Operator] Agent finished, but system is NOT healthy yet.")
                print("   Retrying in 5 seconds...")
                time.sleep(5)

            except (OSError, subprocess.SubprocessError) as e:
                logger.error(f"❌ Execution error: {e}")
                time.sleep(5)

        print(f"\n❌ [SDS Operator] Failed to heal system after {max_retries} attempts.")
        return False
