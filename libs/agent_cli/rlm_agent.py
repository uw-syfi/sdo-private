"""RLM-based coding agent for the SDS operator.

Provides a ``CodingAgent`` implementation that uses the Recursive Language
Model (RLM) paradigm: deployment artifacts are stored as REPL variables that
the LLM can programmatically query via ``execute_code`` actions, reducing
token usage ~50–75% on large logs.

Register with ``provider = "rlm"`` in ``sds.toml``.
"""

import re
from pathlib import Path
from typing import Any

from app_operator.logger import logger
from app_operator.rlm import RecursiveDeploymentAgent, RLMContext
from libs.agent_cli.llm_client import LiteLLMClient
from libs.agent_cli.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol
from libs.agent_cli.utils import FILE_GEN_SYSTEM_PROMPT, generate_and_write_files

from .base import CodingAgent, register_provider
from .events import AgentEventHandler

# Patterns that indicate a file-generation task (write specific output files).
# These tasks use a direct single LLM call instead of the RLM loop.
_FILE_GEN_PATTERNS = [
    r"\.sds/code_analysis\.md",
    r"\.sds/deployment_issues\.md",
    r"\.sds/deploy\.sh",
    r"\.sds/health_check\.sh",
]
_FILE_GEN_RE = re.compile("|".join(_FILE_GEN_PATTERNS))


@register_provider("rlm")
class RLMCodingAgent(CodingAgent):
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
        location: str | None = None,
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
        """
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder: TrajectoryRecorderProtocol = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler
        self.location = location
        self._client = LiteLLMClient(self.model, self.location, self.recorder)

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

        if _FILE_GEN_RE.search(prompt):
            return self._generate_files(prompt, repo_path)

        context: Any = self._build_context(repo_path)
        agent = RecursiveDeploymentAgent(
            trajectory=self.recorder,
            max_recursion_depth=5,
            llm_provider=self.model,
            vertex_location=self.location,
        )
        return agent.run_task(task=prompt, context=context, repo_path=str(repo_path))

    def _generate_files(self, prompt: str, repo_path: Path) -> str:
        """Handle file-generation tasks with a direct litellm call.

        Asks the LLM to return the file content, then writes it to the expected
        output path so the operator's post-call existence check succeeds.
        """
        try:
            raw = self._client.complete(
                [
                    {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                label="rlm file gen",
            )
        except Exception as e:
            logger.error(f"[RLM] Direct LLM call failed: {e}")
            return f"LLM call failed: {e}"

        generate_and_write_files(raw, prompt, repo_path, "[RLM]")
        return raw

    def _build_context(self, repo_path: Path) -> Any:
        """Build an ``RLMContext`` by reading available artifacts from *repo_path*.

        Files that do not exist are silently skipped (empty string).
        """
        sds = repo_path / ".sds"
        return RLMContext(
            error_log=self._read(sds / "logs" / "deploy.log"),
            deployment_script=self._read(sds / "deploy.sh"),
            health_check_output=self._read(sds / "logs" / "health_check.log"),
            dockerfile=self._read(repo_path / "Dockerfile"),
            docker_compose=(
                self._read(repo_path / "docker-compose.yml") or self._read(repo_path / "docker-compose.yaml")
            ),
            readme=(self._read(repo_path / "README.md") or self._read(repo_path / "README.rst")),
            analysis_report=self._read(sds / "code_analysis.md"),
        )

    @staticmethod
    def _read(path: Path) -> str:
        """Read *path* and return its text content, or ``""`` on any error."""
        try:
            if path.exists():
                return path.read_text()
        except Exception:
            pass
        return ""
