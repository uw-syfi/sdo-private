"""RLM-based coding agent for the SDS operator.

Provides a ``CodingAgent`` implementation that uses the Recursive Language
Model (RLM) paradigm: deployment artifacts are stored as REPL variables that
the LLM can programmatically query via ``execute_code`` actions, reducing
token usage ~50–75% on large logs.

Register with ``provider = "rlm"`` in ``sds.toml``.
"""

import litellm

from loguru import logger
from app_operator.rlm.environment import RLMContext
from app_operator.rlm.recursive_agent import RecursiveDeploymentAgent
from libs.agent_cli.trajectory import TrajectoryRecorderProtocol
from libs.agent_cli.rlm_utils import (
    _FILE_GEN_RE,
)

from libs.agent_cli.base import CodingAgent, register_provider
from libs.agent_cli.events import AgentEventHandler
from libs.agent_cli.utils import FILE_GEN_SYSTEM_PROMPT, generate_and_write_files

from pathlib import Path


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
        self.recorder = recorder
        self.event_handler = event_handler
        self.location = location

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

        context = self._build_context(repo_path)
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
        import os

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "cache": {"no-cache": True},
        }
        location = self.location or os.environ.get("VERTEX_LOCATION")
        if location:
            kwargs["vertex_location"] = location

        try:
            response = litellm.completion(**kwargs)
            raw = response.choices[0].message.content or ""
        except Exception as e:
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

        # Create backup of deploy.sh on first build (before any fixes).
        if deploy_sh.exists() and not deploy_bak.exists():
            import shutil
            shutil.copy2(deploy_sh, deploy_bak)

        # original_script comes from the backup (immutable first version).
        original_script = self._read(deploy_bak)

        return RLMContext(
            error_log=self._read(sds / "logs" / "deploy.log"),
            deployment_script=self._read(deploy_sh),
            health_check_output=self._read(sds / "logs" / "health_check.log"),
            dockerfile=self._read(repo_path / "Dockerfile"),
            docker_compose=(
                self._read(repo_path / "docker-compose.yml")
                or self._read(repo_path / "docker-compose.yaml")
            ),
            readme=(
                self._read(repo_path / "README.md")
                or self._read(repo_path / "README.rst")
            ),
            analysis_report=self._read(sds / "code_analysis.md"),
            original_script=original_script,
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
