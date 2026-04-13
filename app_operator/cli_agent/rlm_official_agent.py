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
        """Write *content* to a file inside the repository."""
        full = _resolve_safe(path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        Path(full).write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    def list_files(path: str = ".") -> list[str]:
        """List files/directories under *path* (relative to repo root)."""
        full = _resolve_safe(path)
        try:
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
            "description": "Write content to a file in the repo",
        },
        "list_files": {
            "tool": list_files,
            "description": "List files/directories (path relative to repo root)",
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


_SDS_SYSTEM_PROMPT = """\
You are an SDS (Self-Defining Systems) deployment operator agent.

Your task is to deploy, diagnose, and repair application deployments.
You have access to a repository containing the application source code and
deployment configuration.  Use the provided tools (read_file, write_file,
list_files, run_shell) to explore the repository and fix deployment issues.

The REPO_PATH variable contains the absolute path to the repository.

When fixing deployment errors:
1. First explore the repository structure with list_files.
2. Read error logs and deployment scripts.
3. Identify the root cause.
4. Write corrected deployment scripts to .sds/deploy.sh and/or .sds/health_check.sh.
5. Provide a clear summary of what you fixed and why.

When generating deployment scripts:
1. Analyze the repository to understand the application stack.
2. Generate .sds/deploy.sh (start/stop/restart commands).
3. Generate .sds/health_check.sh (health verification).
4. Make scripts executable-ready with proper shebang lines.

Always write output files using write_file() before providing your final answer.
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

    def _generate_files(self, prompt: str, repo_path: Path) -> str:
        """Handle file-generation tasks with a direct litellm call."""
        from libs.agent_cli.llm_client import LiteLLMClient

        client = LiteLLMClient(f"gemini/{self.model}", self.location, self.recorder)
        try:
            raw = client.complete(
                [
                    {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                label="rlm-official file gen",
            )
        except (ConnectionError, TimeoutError, RuntimeError) as exc:
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

        # Build model name — the rlm library's gemini backend expects the
        # bare model name (e.g. "gemini-2.5-pro") and reads GEMINI_API_KEY.
        model_name = self.model
        # Strip provider prefix if present (e.g. "gemini/gemini-2.5-pro" -> "gemini-2.5-pro")
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]

        rlm = RLM(
            backend="gemini",
            backend_kwargs={"model_name": model_name},
            other_backends=["gemini"],
            other_backend_kwargs=[{"model_name": model_name}],
            environment="local",
            max_depth=self.max_depth,
            max_iterations=self.max_iterations,
            max_timeout=float(timeout) if timeout else None,
            custom_system_prompt=_SDS_SYSTEM_PROMPT,
            custom_tools=custom_tools,
            logger=rlm_logger,
            verbose=True,
        )

        try:
            result = rlm.completion(prompt=context_text, root_prompt=prompt)

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
