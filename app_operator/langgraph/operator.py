import signal
import threading
import time
from pathlib import Path
from typing import Optional

from app_operator.config import load_config, Config
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.langgraph.llm import build_llm
from app_operator.langgraph.graph import build_graph
from tools.trajectory import init_trajectory, finalize_trajectory


class LangGraphOperator:
    """LangGraph-based operator for deployment and monitoring."""

    def __init__(
        self,
        repo_path: str,
        health_check_interval: int = 30,
        health_check_max_count: Optional[int] = 5,
        max_deployment_attempts: int = 5,
        filesystem: Optional[FileSystemInterface] = None,
        config: Optional[Config] = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval
        self.health_check_max_count = health_check_max_count
        self.max_deployment_attempts = max_deployment_attempts
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()

        if not self.filesystem.exists(self.repo_path):
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not self.filesystem.is_dir(self.repo_path):
            raise ValueError(f"Repository path is not a directory: {repo_path}")

        if config is None:
            self.config = load_config(str(self.repo_path))
        else:
            self.config = config

        self.sds_dir = self.repo_path / ".sds"
        self._persist_deployment_config()

        # Initialize trajectory recorder
        self.trajectory = init_trajectory(self.repo_path)
        self.trajectory.set_agent_name("LangGraph")

        try:
            self.llm = build_llm(self.config)
        except Exception as e:
            raise AgentError(f"Failed to initialize LangGraph LLM: {e}")

        self._shutdown_requested = False
        self._deployed = False

        self.graph = build_graph(
            self.llm,
            repo_path=self.repo_path,
            config=self.config,
            health_check_interval=self.health_check_interval,
            filesystem=self.filesystem,
            check_shutdown=lambda: self._shutdown_requested,
        )

    def _persist_deployment_config(self) -> None:
        if not self.filesystem.exists(self.sds_dir):
            self.filesystem.mkdir(self.sds_dir)

        sds_config_path = self.sds_dir / "config.toml"
        if not self.filesystem.exists(sds_config_path):
            logger.info(f"Creating deployment config at {sds_config_path}")
            config_content = (
                "[deployment]\n"
                f'platform = "{self.config.deployment.platform}"\n'
                f'target = "{self.config.deployment.target}"\n'
            )
            self.filesystem.write_text(sds_config_path, config_content)

    def run(self) -> int:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        try:
            logger.info("Starting LangGraph App Operator Mode")
            logger.info(f"Repository: {self.repo_path}")
            logger.info("Agent: LangGraph")
            logger.info(f"Deployment Platform: {self.config.deployment.platform}")
            logger.info(f"Deployment Target: {self.config.deployment.target}")

            initial_state = {
                "messages": [],
                "repo_path": str(self.repo_path),
                "attempt": 1,
                "max_attempts": self.max_deployment_attempts,
                "analysis_done": False,
                "scripts_done": False,
                "deploy_result": None,
                "health_result": None,
                "monitor_count": 0,
                "monitor_max": self.health_check_max_count,
                "analysis_summary": None,
                "last_fix_summary": None,
                "token_usage": {"input": 0, "output": 0, "total": 0},
            }

            thread_id = str(int(time.time()))
            final_state = self.graph.invoke(
                initial_state, config={"configurable": {"thread_id": thread_id}}
            )

            if final_state:
                usage = final_state.get("token_usage", {})
                logger.info(f"Total Token Usage: {usage}")

            health_result = final_state.get("health_result") if final_state else None
            self._deployed = bool(health_result and health_result.get("success"))
            finalize_trajectory("completed" if self._deployed else "failed")
            return 0

        except KeyboardInterrupt:
            logger.info("Shutting down due to interrupt...")
            finalize_trajectory("interrupted")
            return 1

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            import traceback

            traceback.print_exc()
            finalize_trajectory("failed")
            return 1

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info(
                f"Received {signal_name} signal. Initiating graceful shutdown..."
            )
            if signum == signal.SIGINT:
                raise KeyboardInterrupt()
