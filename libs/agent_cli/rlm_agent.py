"""RLM-based coding agent for the SDS operator.

Provides a ``CodingAgent`` implementation that uses the Recursive Language
Model (RLM) paradigm: deployment artifacts are stored as REPL variables that
the LLM can programmatically query via ``execute_code`` actions, reducing
token usage ~50–75% on large logs.

Register with ``provider = "rlm"`` in ``sds.toml``.
"""

import re
from pathlib import Path

import litellm
from app_operator.rlm.environment import RLMContext

from app_operator.logger import logger
from app_operator.rlm.recursive_agent import RecursiveDeploymentAgent
from app_operator.trajectory import TrajectoryRecorderProtocol

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

        # Extract all expected .sds/<file> paths from the prompt.
        expected_files = re.findall(r"\.sds/[\w._-]+", prompt)

        system_msg = (
            "You are a deployment assistant. The user will ask you to generate "
            "one or more files. For EACH file, output a section in this exact format:\n\n"
            "FILE: .sds/<filename>\n"
            "```\n"
            "<file content here>\n"
            "```\n\n"
            "Output ONLY these sections. Do not add explanations outside the sections."
        )

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_msg},
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

        # Parse FILE: sections and write each file.
        written = []
        file_sections = re.findall(
            r"FILE:\s*(\.sds/[\w._-]+)\s*\n```[^\n]*\n(.*?)```",
            raw,
            re.DOTALL,
        )
        for rel_path, content in file_sections:
            out_path = repo_path / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[RLM] Wrote {out_path}")
            written.append(rel_path)

        # Fallback: if no FILE: sections but there's a single expected file,
        # write the entire response as that file's content.
        if not written and len(expected_files) == 1:
            out_path = repo_path / expected_files[0]
            # Strip markdown code fences if present.
            content = re.sub(r"^```[^\n]*\n|```$", "", raw.strip(), flags=re.MULTILINE)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[RLM] Wrote {out_path} (fallback)")
            written.append(expected_files[0])

        return raw

    def _build_context(self, repo_path: Path) -> RLMContext:
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
                self._read(repo_path / "docker-compose.yml")
                or self._read(repo_path / "docker-compose.yaml")
            ),
            readme=(
                self._read(repo_path / "README.md")
                or self._read(repo_path / "README.rst")
            ),
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
