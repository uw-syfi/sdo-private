"""Official RLM library-based coding agent for the SDS operator.

Wraps the ``rlm`` package (``pip install rlms``) to provide depth-2 recursive
language model completion with a Python REPL.  The root LM can delegate
sub-problems to child RLMs that each have their own exploratory REPL.

Custom tools give the REPL access to SDS filesystem/shell utilities so the
model can read files, list directories, run shell commands, and write
deployment artifacts.

Register with ``provider = "rlm-official"`` in ``sds.toml``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from app_operator.cli_agent._rlm_utils import _FILE_GEN_RE, _FIX_ERROR_RE
from libs.agent_cli.base import CodingAgent, register_provider
from libs.agent_cli.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol
from libs.agent_cli.utils import FILE_GEN_SYSTEM_PROMPT, generate_and_write_files

if TYPE_CHECKING:
    from libs.agent_cli.events import AgentEventHandler


def _build_custom_tools(repo_path: Path) -> dict[str, Any]:
    """Build the custom_tools dict injected into the RLM REPL namespace.

    All tools are sandboxed to *repo_path* — file and shell operations refuse
    to escape the repository root.
    """
    root = str(repo_path.resolve())

    def _resolve_safe(path: str) -> str:
        """Resolve *path* relative to repo root and reject escapes."""
        resolved = os.path.realpath(os.path.join(root, path))
        if not resolved.startswith(root + os.sep) and resolved != root:
            raise PermissionError(f"Access denied: {path!r} resolves outside the repo")
        return resolved

    def read_file(path: str) -> str:
        """Read a file from the repository.  *path* is relative to repo root."""
        full = _resolve_safe(path)
        try:
            return Path(full).read_text()
        except OSError as exc:
            return f"Error reading {path}: {exc}"

    def write_file(path: str, content: str) -> str:
        """Write *content* to a file inside the repository (overwrites)."""
        full = _resolve_safe(path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        Path(full).write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    def append_file(path: str, content: str) -> str:
        """Append *content* to a file (creates if missing). Use this to build long files incrementally."""
        full = _resolve_safe(path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        with open(full, "a") as f:
            f.write(content)
        return f"Appended {len(content)} bytes to {path}"

    def list_files(path: str = ".", recursive: bool = False) -> list[str]:
        """List files/directories under *path* (relative to repo root).

        If *recursive* is True, walk the tree and return all file paths.
        """
        full = _resolve_safe(path)
        try:
            if recursive:
                result = []
                for dirpath, _dirs, files in os.walk(full):
                    for f in files:
                        rel = os.path.relpath(os.path.join(dirpath, f), full)
                        result.append(rel)
                return sorted(result)
            return sorted(os.listdir(full))
        except OSError as exc:
            return [f"Error listing {path}: {exc}"]

    def run_shell(cmd: str, timeout: int = 60) -> str:
        """Run a shell command inside the repository root.

        Returns combined stdout+stderr, truncated to 20 000 chars.
        """
        try:
            result = subprocess.run(  # noqa: S602
                cmd,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            if len(output) > 20_000:
                output = output[:20_000] + "\n... [truncated]"
            return output
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s: {cmd}"
        except OSError as exc:
            return f"Error running command: {exc}"

    return {
        "read_file": {
            "tool": read_file,
            "description": "Read a file from the repo (path relative to repo root)",
        },
        "write_file": {
            "tool": write_file,
            "description": "Write content to a file in the repo (overwrites)",
        },
        "append_file": {
            "tool": append_file,
            "description": "Append content to a file (creates if missing). Use for building long files incrementally.",
        },
        "list_files": {
            "tool": list_files,
            "description": "List files/directories (path relative to repo root). Pass recursive=True for full tree.",
        },
        "run_shell": {
            "tool": run_shell,
            "description": "Run a shell command in the repo root",
        },
        "REPO_PATH": {
            "tool": root,
            "description": "Absolute path to the target repository",
        },
    }


def _build_context_text(repo_path: Path) -> str:
    """Build a context string from available SDS artifacts for the RLM prompt."""
    sds = repo_path / ".sds"

    def _read(p: Path) -> str:
        try:
            return p.read_text() if p.exists() else ""
        except OSError:
            return ""

    parts: list[str] = []
    parts.append(f"Repository: {repo_path}")

    # Code analysis
    analysis = _read(sds / "code_analysis.md")
    if analysis:
        parts.append(f"\n=== Code Analysis ===\n{analysis}")

    # Deploy script
    deploy_sh = _read(sds / "deploy.sh")
    if deploy_sh:
        parts.append(f"\n=== deploy.sh ===\n{deploy_sh}")

    # Health check script
    health_sh = _read(sds / "health_check.sh")
    if health_sh:
        parts.append(f"\n=== health_check.sh ===\n{health_sh}")

    # Latest deploy log
    logs = sds / "logs"
    deploy_log = _find_latest_log(logs, "deploy_attempt_", "deploy.log")
    if deploy_log:
        parts.append(f"\n=== Latest Deploy Log ===\n{deploy_log}")

    # Latest health check log
    health_log = _find_latest_log(logs, "health_check_attempt_", "health_check.log")
    if health_log:
        parts.append(f"\n=== Latest Health Check Log ===\n{health_log}")

    # Dockerfile
    dockerfile = _read(repo_path / "Dockerfile")
    if dockerfile:
        parts.append(f"\n=== Dockerfile ===\n{dockerfile}")

    # docker-compose
    compose = _read(repo_path / "docker-compose.yml") or _read(repo_path / "docker-compose.yaml")
    if compose:
        parts.append(f"\n=== docker-compose.yml ===\n{compose}")

    # README
    readme = _read(repo_path / "README.md") or _read(repo_path / "README.rst")
    if readme:
        parts.append(f"\n=== README ===\n{readme}")

    return "\n".join(parts)


def _find_latest_log(logs_dir: Path, prefix: str, fallback_name: str) -> str:
    """Find the latest numbered log file or fall back to a default name."""
    if not logs_dir.exists():
        return ""

    import re

    numbered: list[tuple[int, Path]] = []
    for p in logs_dir.iterdir():
        m = re.match(rf"^{re.escape(prefix)}(\d+)\.log$", p.name)
        if m:
            numbered.append((int(m.group(1)), p))
    numbered.sort(key=lambda t: t[0])

    if numbered:
        try:
            return numbered[-1][1].read_text()
        except OSError:
            return ""

    fallback = logs_dir / fallback_name
    try:
        return fallback.read_text() if fallback.exists() else ""
    except OSError:
        return ""


_SDS_ROOT_PROMPT_PREFIX = """\
You are an SDS deployment operator agent. Your task is to deploy, diagnose,
and repair application deployments.

Use the provided tools (read_file, write_file, append_file, list_files, run_shell)
and the REPO_PATH variable to explore the repository.

IMPORTANT: When writing long files (markdown reports, scripts), build them
incrementally using append_file() in multiple code blocks. Do NOT try to define
very long strings in a single code block — this causes SyntaxErrors from
unterminated triple-quoted strings. Instead:
  write_file('path', '')  # clear the file
  append_file('path', 'section 1 content\\n')
  append_file('path', 'section 2 content\\n')

CRITICAL RULES for deploy.sh and health_check.sh scripts:
- PROJECT_NAME MUST be lowercased. Docker Compose rejects uppercase.
  Use: PROJECT_NAME=$(basename "$APP_DIR" | tr '[:upper:]' '[:lower:]')
- Always use: docker compose --project-name "$PROJECT_NAME" ...
- Build context paths: verify they exist with list_files() before using them.

When fixing deployment errors:
1. Explore the repository structure with list_files().
2. Read error logs and deployment scripts with read_file().
3. Identify the root cause — do NOT re-run deploy.sh yourself.
   The outer pipeline will re-run deployment after your fix.
4. Write corrected scripts using write_file() or append_file().

When generating deployment scripts or analysis:
1. Analyze the repo to understand the application stack.
2. Write output files before submitting your final answer.

"""


@register_provider("rlm-official")
class RLMOfficialAgent(CodingAgent):
    """Coding agent wrapping the official ``rlm`` library (``pip install rlms``).

    Uses depth-2 RLM recursion: the root LM can delegate sub-problems to
    child RLMs that each get their own Python REPL for iterative exploration.

    For file-generation tasks (code analysis, deploy.sh creation) the agent
    falls back to a single litellm call, matching the existing RLM agent
    behaviour.
    """

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        location: str | None = None,
        dspy_config: object | None = None,
        max_depth: int = 2,
        max_iterations: int = 30,
    ):
        self.model = model or "gemini-2.5-pro"
        self.recorder: TrajectoryRecorderProtocol = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler
        self.location = location
        self.dspy_config = dspy_config
        self.max_depth = max_depth
        self.max_iterations = max_iterations

    def _resolve_backend(self) -> tuple[str, dict[str, Any]]:
        """Pick the RLM backend based on available credentials.

        Uses the native ``gemini`` backend when ``GEMINI_API_KEY`` is set,
        otherwise falls back to ``litellm`` which supports Vertex AI via
        Application Default Credentials.
        """
        model_name = self.model
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]

        if os.environ.get("GEMINI_API_KEY"):
            logger.info("[RLM-Official] Using gemini backend (GEMINI_API_KEY)")
            return "gemini", {"model_name": model_name}

        # Fall back to litellm which handles Vertex AI auth via ADC
        litellm_model = f"vertex_ai/{model_name}"
        logger.info("[RLM-Official] Using litellm backend (Vertex AI): {}", litellm_model)
        return "litellm", {"model_name": litellm_model}

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        repo_path = Path(cwd) if cwd else Path.cwd()

        # File-generation tasks use a direct LLM call (same as existing RLM agent)
        if not _FIX_ERROR_RE.search(prompt) and _FILE_GEN_RE.search(prompt):
            return self._generate_files(prompt, repo_path)

        return self._run_rlm(prompt, repo_path, timeout)

    # ------------------------------------------------------------------
    # File generation (direct LLM call, no REPL)
    # ------------------------------------------------------------------

    _FILE_GEN_EXTRA = (
        "\n\nCRITICAL: In deploy.sh and health_check.sh, the PROJECT_NAME "
        "variable MUST be lowercased. Docker Compose rejects uppercase "
        "characters in project names. Always use:\n"
        '  PROJECT_NAME=$(basename "$APP_DIR" | tr \'[:upper:]\' \'[:lower:]\' '
        "| tr -c '[:alnum:]-' '-')\n"
        "Also verify that all docker compose build context paths actually exist "
        "in the repository before referencing them."
    )

    def _generate_files(self, prompt: str, repo_path: Path) -> str:
        """Handle file-generation tasks with a direct litellm call."""
        from libs.agent_cli.llm_client import LiteLLMClient

        # Use the same backend resolution as the RLM path
        _, bk = self._resolve_backend()
        litellm_model = bk["model_name"]
        client = LiteLLMClient(litellm_model, self.location, self.recorder)
        try:
            raw = client.complete(
                [
                    {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT + self._FILE_GEN_EXTRA},
                    {"role": "user", "content": prompt},
                ],
                label="rlm-official file gen",
            )
        except Exception as exc:
            logger.error(f"[RLM-Official] Direct LLM call failed: {exc}")
            return f"LLM call failed: {exc}"

        generate_and_write_files(raw, prompt, repo_path, "[RLM-Official]")
        return raw

    # ------------------------------------------------------------------
    # RLM completion (depth-2 recursion with REPL)
    # ------------------------------------------------------------------

    def _run_rlm(self, prompt: str, repo_path: Path, timeout: int) -> str:
        """Run the official RLM library for fix/analysis tasks."""
        try:
            from rlm import RLM  # type: ignore[import-not-found]
            from rlm.logger import RLMLogger  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.error(f"[RLM-Official] rlm library not installed: {exc}")
            return f"rlm library not available: {exc}"

        custom_tools = _build_custom_tools(repo_path)
        context_text = _build_context_text(repo_path)

        log_dir = str(repo_path / ".sds" / "rlm_logs")
        os.makedirs(log_dir, exist_ok=True)
        rlm_logger = RLMLogger(log_dir=log_dir)

        backend, backend_kwargs = self._resolve_backend()

        rlm = RLM(
            backend=backend,
            backend_kwargs=backend_kwargs,
            other_backends=[backend],
            other_backend_kwargs=[backend_kwargs],
            environment="local",
            max_depth=self.max_depth,
            max_iterations=self.max_iterations,
            max_timeout=float(timeout) if timeout else None,
            custom_tools=custom_tools,
            logger=rlm_logger,
            verbose=True,
        )

        root_prompt = _SDS_ROOT_PROMPT_PREFIX + prompt
        try:
            result = rlm.completion(prompt=context_text, root_prompt=root_prompt)

            # Record usage in trajectory
            self._record_usage(result)

            answer = result.response
            logger.info(f"[RLM-Official] Completed in {result.execution_time:.1f}s")
            return answer

        except KeyboardInterrupt:
            logger.warning("[RLM-Official] Interrupted by user")
            raise
        except Exception as exc:
            logger.error(f"[RLM-Official] RLM completion failed: {exc}")
            return f"RLM completion failed: {exc}"
        finally:
            rlm.close()

    def _record_usage(self, result: Any) -> None:
        """Record RLM token usage into the SDS trajectory recorder."""
        if not hasattr(result, "usage_summary") or result.usage_summary is None:
            return

        try:
            usage = result.usage_summary
            total_input = 0
            total_output = 0
            for _model, model_usage in usage.model_usage_summaries.items():
                total_input += model_usage.total_input_tokens
                total_output += model_usage.total_output_tokens

            token_data = {
                "prompt_tokens": total_input,
                "completion_tokens": total_output,
                "total_tokens": total_input + total_output,
            }
            if hasattr(self.recorder, "record_token_usage"):
                self.recorder.record_token_usage(token_data)  # type: ignore[arg-type]

            logger.info(
                f"[RLM-Official] Tokens: {total_input} in, {total_output} out, {total_input + total_output} total"
            )
        except (AttributeError, TypeError) as exc:
            logger.warning(f"[RLM-Official] Failed to record token usage: {exc}")
