"""RLM-based coding agent for the SDS operator.

Provides a ``CodingAgent`` implementation that uses the Recursive Language
Model (RLM) paradigm: deployment artifacts are stored as REPL variables that
the LLM can programmatically query via ``execute_code`` actions, reducing
token usage ~50–75% on large logs.

This class is retained as an internal building block for hybrid-style flows.
"""

import re
from pathlib import Path
from typing import Any

from agentshim.events import AgentEventHandler
from agentshim.utils import FILE_GEN_SYSTEM_PROMPT, generate_and_write_files
from loguru import logger

from agentshim import BaseCodingAgent
from app_operator.cli_agent._event_handlers import compose_event_handlers
from app_operator.cli_agent._rlm_utils import FILE_GEN_RE, FIX_ERROR_RE
from app_operator.cli_agent.rlm.environment import RLMContext
from app_operator.cli_agent.rlm.recursive_agent import RecursiveDeploymentAgent
from app_operator.prompts import DSPyConfigProtocol
from app_operator.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol
from libs.llm_rt import LiteLLMClient


class RLMCodingAgent(BaseCodingAgent):
    """Coding agent that uses the RLM paradigm for efficient context handling.

    Instead of passing full log files as text in every prompt, it exposes them
    as Python variables inside a REPL so the LLM can filter/query them with
    ``execute_code`` actions before forming a final answer.

    For simple file-generation tasks (code analysis, deploy.sh generation) the
    agent falls back to a single direct LLM call so the model can return content
    without getting stuck in the exploration loop.
    """

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        event_handlers: list[AgentEventHandler] | None = None,
        location: str | None = None,
        dspy_config: DSPyConfigProtocol | None = None,
        rlm_mode: str = "compatibility",
    ):
        """Initialise the RLM coding agent.

        Args:
            model: litellm-compatible model string, e.g.
                ``"vertex_ai/gemini-2.0-flash"``.  Defaults to
                ``"vertex_ai/gemini-2.0-flash"``.
            recorder: Optional trajectory recorder (attached post-construction
                by the operator if not supplied here).
            event_handler: Optional event handler (unused for RLM but kept for
                interface consistency).
            location: Vertex AI location (e.g. ``"global"``, ``"us-central1"``).
                Forwarded as ``vertex_location`` to litellm.
            dspy_config: Optional DSPy configuration for optimised prompts.
        """
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder: Any = recorder or NullTrajectoryRecorder()
        self.event_handler = compose_event_handlers(event_handler, event_handlers)
        self.location = location
        self.dspy_config = dspy_config
        self.rlm_mode = rlm_mode
        self._client = LiteLLMClient(self.model, self.location, recorder)

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        """Generate a response using the RLM loop or a direct LLM call.

        File-generation tasks (those whose prompt references `.sds/` output
        files) use a single direct litellm call so the model returns the file
        content without getting stuck in an exploration loop.  All other tasks
        use the full RLM loop.

        Args:
            prompt: Task / rendered prompt from the operator.
            cwd: Working directory (used to locate ``.sds/`` artifacts).
            timeout: Ignored for RLM (litellm handles its own timeouts).
            silent: Ignored for RLM (no streaming CLI output).

        Returns:
            Final answer string produced by the LLM.
        """
        repo_path = Path(cwd) if cwd else Path.cwd()

        # Fix-error prompts mention .sds/deploy.sh as context but must go
        # through the RLM loop, not the file-generation path.
        if not FIX_ERROR_RE.search(prompt) and FILE_GEN_RE.search(prompt):
            return self._generate_files(prompt, repo_path)

        context = self._build_context(repo_path)
        agent = RecursiveDeploymentAgent(
            trajectory=self.recorder,
            max_recursion_depth=5,
            llm_provider=self.model,
            vertex_location=self.location,
            max_consecutive_errors=3,
            dspy_config=self.dspy_config,
            rlm_mode=self.rlm_mode,
        )
        return agent.run_task(task=prompt, context=context, repo_path=str(repo_path))

    def _generate_files(self, prompt: str, repo_path: Path) -> str:
        """Handle file-generation tasks with a direct litellm call.

        Asks the LLM to return the file content, then writes it to the expected
        output path so the operator's post-call existence check succeeds.
        Token tracking happens automatically via ``self._client``.
        """
        try:
            raw = self._client.complete(
                [
                    {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                label="rlm file gen",
            )
        except (ConnectionError, TimeoutError, RuntimeError) as e:
            logger.error(f"[RLM] Direct LLM call failed: {e}")
            return f"LLM call failed: {e}"

        generate_and_write_files(raw, prompt, repo_path, "[RLM]")
        return raw

    def _build_context(self, repo_path: Path) -> RLMContext:
        """Build an ``RLMContext`` by reading available artifacts from *repo_path*.

        Files that do not exist are silently skipped (empty string).

        On the first call where ``.sds/deploy.sh`` exists, a backup is created
        at ``.sds/deploy.sh.bak`` so the original script is preserved across
        fix iterations.  Subsequent calls read from the backup.
        """
        sds = repo_path / ".sds"
        deploy_sh = sds / "deploy.sh"
        deploy_bak = sds / "deploy.sh.bak"
        logs_dir = sds / "logs"

        # Create backup of deploy.sh on first build (before any fixes).
        if deploy_sh.exists() and not deploy_bak.exists():
            import shutil

            shutil.copy2(deploy_sh, deploy_bak)

        # original_script comes from the backup (immutable first version).
        original_script = self._read(deploy_bak)
        deploy_attempt_logs = self._sorted_attempt_logs(
            logs_dir,
            pattern="deploy_attempt_*.log",
            prefix="deploy_attempt_",
        )
        latest_deploy_log = deploy_attempt_logs[-1] if deploy_attempt_logs else (logs_dir / "deploy.log")

        health_attempt_logs = self._sorted_attempt_logs(
            logs_dir,
            pattern="health_check_attempt_*.log",
            prefix="health_check_attempt_",
        )
        recheck_attempt_logs = self._sorted_attempt_logs(
            logs_dir,
            pattern="health_recheck_attempt_*.log",
            prefix="health_recheck_attempt_",
        )
        latest_health_log = logs_dir / "health_check.log"
        if health_attempt_logs or recheck_attempt_logs:
            health_max_attempt = (
                self._extract_attempt_number(
                    health_attempt_logs[-1].name,
                    "health_check_attempt_",
                )
                if health_attempt_logs
                else 0
            )
            recheck_max_attempt = (
                self._extract_attempt_number(
                    recheck_attempt_logs[-1].name,
                    "health_recheck_attempt_",
                )
                if recheck_attempt_logs
                else 0
            )
            max_attempt = max(health_max_attempt or 0, recheck_max_attempt or 0)
            recheck_for_max = logs_dir / f"health_recheck_attempt_{max_attempt}.log"
            health_for_max = logs_dir / f"health_check_attempt_{max_attempt}.log"
            if recheck_for_max.exists():
                latest_health_log = recheck_for_max
            elif health_for_max.exists():
                latest_health_log = health_for_max

        previous_attempts: list[dict[str, Any]] = []
        for log_path in deploy_attempt_logs:
            attempt = self._extract_attempt_number(log_path.name, "deploy_attempt_")
            if attempt is None:
                continue

            attempt_data: dict[str, Any] = {
                "attempt": attempt,
                "deploy_log": self._read(log_path),
            }
            fix_summary = logs_dir / f"fix_summary_{attempt}.log"
            if fix_summary.exists():
                attempt_data["fix_summary"] = self._read(fix_summary)
            health_attempt = logs_dir / f"health_check_attempt_{attempt}.log"
            if health_attempt.exists():
                attempt_data["health_check_log"] = self._read(health_attempt)
            health_recheck = logs_dir / f"health_recheck_attempt_{attempt}.log"
            if health_recheck.exists():
                attempt_data["health_recheck_log"] = self._read(health_recheck)
            previous_attempts.append(attempt_data)

        if previous_attempts:
            attempt_number = max(a["attempt"] for a in previous_attempts) + 1
        else:
            attempt_number = 1

        return RLMContext(
            error_log=self._read(latest_deploy_log),
            deployment_script=self._read(deploy_sh),
            health_check_output=self._read(latest_health_log),
            previous_attempts=previous_attempts,
            dockerfile=self._read(repo_path / "Dockerfile"),
            docker_compose=(
                self._read(repo_path / "docker-compose.yml") or self._read(repo_path / "docker-compose.yaml")
            ),
            readme=(self._read(repo_path / "README.md") or self._read(repo_path / "README.rst")),
            analysis_report=self._read(sds / "code_analysis.md"),
            original_script=original_script,
            attempt_number=attempt_number,
        )

    @staticmethod
    def _read(path: Path) -> str:
        """Read *path* and return its text content, or ``""`` on any error."""
        try:
            if path.exists():
                return path.read_text()
        except OSError:
            pass
        return ""

    @staticmethod
    def _extract_attempt_number(filename: str, prefix: str) -> int | None:
        """Extract attempt number from filenames like ``prefix{n}.log``."""
        match = re.match(rf"^{re.escape(prefix)}(\d+)\.log$", filename)
        if not match:
            return None
        return int(match.group(1))

    def _sorted_attempt_logs(
        self,
        logs_dir: Path,
        pattern: str,
        prefix: str,
    ) -> list[Path]:
        """Return attempt logs sorted by attempt number."""
        logs: list[tuple[int, Path]] = []
        for path in logs_dir.glob(pattern):
            attempt = self._extract_attempt_number(path.name, prefix)
            if attempt is None:
                continue
            logs.append((attempt, path))
        logs.sort(key=lambda item: item[0])
        return [path for _attempt, path in logs]
