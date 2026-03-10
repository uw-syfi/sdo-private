import signal
import threading
import time
from pathlib import Path

from app_operator.config import Config, load_config
from app_operator.constants import LANGGRAPH_OUTER_RECURSION_LIMIT
from app_operator.exceptions import AgentError
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.langgraph._llm import build_llm
from app_operator.langgraph.graph import build_graph
from app_operator.logger import logger
from app_operator.operator_base import OperatorBase
from app_operator.progress import emit_progress
from app_operator.trajectory import TrajectoryRecorder


class LangGraphOperator(OperatorBase):
    """LangGraph-based operator for deployment and monitoring."""

    def __init__(
        self,
        repo_path: str,
        health_check_interval: int = 30,
        health_check_max_count: int | None = 5,
        max_deployment_attempts: int = 5,
        filesystem: FileSystemInterface | None = None,
        config: Config | None = None,
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

        if not self.config.agent.provider:
            raise ValueError("agent.provider must be set for langgraph runtime")
        if not self.config.agent.model:
            raise ValueError("agent.model must be set for langgraph runtime")

        self.sds_dir = self.repo_path / ".sds"
        self._persist_deployment_config()

        # Initialize trajectory recorder
        self.recorder = TrajectoryRecorder(self.repo_path)
        self.recorder.set_agent_name("LangGraph")

        try:
            self.llm = build_llm(self.config)
        except (ImportError, ValueError, RuntimeError) as e:
            raise AgentError(f"Failed to initialize LangGraph LLM: {e}") from e

        self._shutdown_requested = False
        self._deployed = False

        self.graph = build_graph(
            self.llm,
            repo_path=self.repo_path,
            config=self.config,
            health_check_interval=self.health_check_interval,
            filesystem=self.filesystem,
            check_shutdown=lambda: self._shutdown_requested,
            recorder=self.recorder,
        )

    def run(self) -> int:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        _status = "failed"
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
                "analysis_done": not self.config.operator.phase.code_analysis,
                "scripts_done": False,
                "deploy_result": None,
                "health_verdict": None,
                "monitor_count": 0,
                "monitor_max": self.health_check_max_count,
                "health_monitoring": self.config.operator.phase.health_monitoring,
                "analysis_summary": None,
                "agent_token_usage": [],
            }

            thread_id = str(int(time.time()))
            invoke_config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": LANGGRAPH_OUTER_RECURSION_LIMIT,
            }
            final_state = self.graph.invoke(initial_state, config=invoke_config)  # type: ignore[reportArgumentType]

            if final_state:
                sessions = final_state.get("agent_token_usage", [])
                totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                for s in sessions:
                    totals["prompt_tokens"] += s.get("input", 0)
                    totals["completion_tokens"] += s.get("output", 0)
                    totals["total_tokens"] += s.get("total", 0)
                logger.info(f"Total Token Usage: {totals}")

                def _log_usage(entry: dict, indent: str = "  ") -> None:
                    own = f", own: in={entry['own_input']}, out={entry['own_output']}" if entry.get("subagents") else ""
                    logger.info(
                        f"{indent}{entry['agent']}: {entry['total']} tokens "
                        f"(in={entry['input']}, out={entry['output']}{own})"
                    )
                    for sub in entry.get("subagents", []):
                        _log_usage(sub, indent + "  ")

                for s in sessions:
                    _log_usage(s)

            health_verdict = final_state.get("health_verdict") if final_state else None
            self._deployed = bool(health_verdict and health_verdict.get("healthy"))
            _status = "completed" if self._deployed else "failed"
            emit_progress("finishing")
            return 0

        except KeyboardInterrupt:
            logger.info("Shutting down due to interrupt...")
            _status = "interrupted"
            return 1

        except (AgentError, OSError, RuntimeError, ValueError) as e:
            # Top-level catch to prevent uncaught exception — specific types are too numerous
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return 1
        finally:
            self.recorder.finalize(_status)

    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        if not self._shutdown_requested:
            self._shutdown_requested = True
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            logger.info(f"Received {signal_name} signal. Initiating graceful shutdown...")
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
