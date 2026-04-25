"""Subagent-based coding agent for the SDS operator.

Instead of a single RLM REPL loop, this agent calls four sequential subagents
that each analyse a different slice of context (trajectory, error logs,
deploy script, repository).  Their summaries are fed to a root LLM call that
produces the final fix.

Register with ``provider = "subagent"`` in ``sds.toml``.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from agentshim.base import register_provider
from agentshim.events import AgentEventHandler
from agentshim.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol
from agentshim.utils import FILE_GEN_SYSTEM_PROMPT, generate_and_write_files
from loguru import logger

from agentshim import BaseCodingAgent
from app_operator.cli_agent._event_handlers import compose_event_handlers
from app_operator.cli_agent._rlm_utils import DIRECT_TEXT_RE, FILE_GEN_RE, FIX_ERROR_RE
from app_operator.cli_agent._subagent_utils import call_subagent
from app_operator.prompts import (
    DSPyConfigProtocol,
    render_error_log_analyst_prompt,
    render_repo_analyst_prompt,
    render_root_synthesis_prompt,
    render_script_analyst_prompt,
    render_trajectory_analyst_prompt,
)
from libs.llm_rt import litellm_call_with_retry


@register_provider("subagent")
class SubagentCodingAgent(BaseCodingAgent):
    """Coding agent that fans out independent subagents for fix tasks.

    For file-generation and direct-text tasks the behaviour matches
    ``RLMCodingAgent`` (single litellm call).  For fix tasks the agent:

    1. Fans out 4 independent subagent calls (trajectory analyst, error log
       analyst, script analyst, repo analyst) — each receives a focused
       slice of context and returns a short summary.
    2. Feeds all 4 summaries plus the original task prompt to a root LLM
       call that produces the actual fix.
    """

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        event_handlers: list[AgentEventHandler] | None = None,
        location: str | None = None,
        dspy_config: DSPyConfigProtocol | None = None,
    ):
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder: TrajectoryRecorderProtocol = recorder or NullTrajectoryRecorder()
        self.event_handler = compose_event_handlers(event_handler, event_handlers)
        self.location = location
        self.dspy_config = dspy_config
        self._total_token_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        repo_path = Path(cwd) if cwd else Path.cwd()
        call_tokens: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        is_fix = FIX_ERROR_RE.search(prompt)
        if not is_fix and DIRECT_TEXT_RE.search(prompt):
            result = self._generate_direct(prompt, call_tokens)
        elif not is_fix and FILE_GEN_RE.search(prompt):
            result = self._generate_files(prompt, repo_path, call_tokens)
        else:
            result = self._generate_fix(prompt, repo_path, call_tokens)

        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._total_token_usage[k] += call_tokens[k]

        if self.recorder and hasattr(self.recorder, "record_token_usage"):
            self.recorder.record_token_usage(self._total_token_usage.copy())  # type: ignore[reportArgumentType]

        return result

    # -- Direct / file-gen paths (same as RLMCodingAgent) ---------------------

    def _generate_direct(self, prompt: str, token_acc: dict[str, int] | None = None) -> str:
        import os

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "cache": {"no-cache": True},
        }
        loc = self.location or os.environ.get("VERTEX_LOCATION")
        if loc:
            kwargs["vertex_location"] = loc

        try:
            return litellm_call_with_retry(kwargs, label="direct text generation", token_acc=token_acc)
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.error(f"[Subagent] Direct text LLM call failed: {type(e).__name__}: {e}")
            return f"LLM call failed: {type(e).__name__}: {e}"

    def generate_direct(self, prompt: str, token_acc: dict[str, int] | None = None) -> str:
        """Public wrapper for the direct-text path."""
        return self._generate_direct(prompt, token_acc)

    def _generate_files(self, prompt: str, repo_path: Path, token_acc: dict[str, int] | None = None) -> str:
        import os

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": FILE_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "cache": {"no-cache": True},
        }
        loc = self.location or os.environ.get("VERTEX_LOCATION")
        if loc:
            kwargs["vertex_location"] = loc

        try:
            raw = litellm_call_with_retry(kwargs, label="file generation", token_acc=token_acc)
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.error(f"[Subagent] Direct LLM call failed: {type(e).__name__}: {e}")
            return f"LLM call failed: {type(e).__name__}: {e}"

        generate_and_write_files(raw, prompt, repo_path, "[Subagent]")
        return raw

    def generate_files(self, prompt: str, repo_path: Path, token_acc: dict[str, int] | None = None) -> str:
        """Public wrapper for the file-generation path."""
        return self._generate_files(prompt, repo_path, token_acc)

    # -- Fix path: fan-out subagents + root synthesis -------------------------

    def _generate_fix(
        self,
        prompt: str,
        repo_path: Path,
        token_acc: dict[str, int] | None = None,
    ) -> str:
        """Fan out 4 subagent analyses, then synthesise the fix with a root call."""
        sds = repo_path / ".sds"

        trajectory_text = self._read_trajectory(sds)
        error_log = self._read(sds / "logs" / "deploy.log") + "\n" + self._read(sds / "logs" / "health_check.log")
        deploy_script = self._read(sds / "deploy.sh")
        original_script = self._read(sds / "deploy.sh.bak")
        repo_context = self._gather_repo_context(repo_path, sds)

        logger.info("[Subagent] Starting sequential subagent analysis (4 subagents)")

        # --- Step 1: Sequential subagent calls (each analyses one context slice) ---
        summaries: dict[str, str] = {}

        try:
            summaries["trajectory"] = call_subagent(
                model=self.model,
                system_prompt=render_trajectory_analyst_prompt(
                    data_description="deployment trajectory JSON",
                    dspy_config=self.dspy_config,
                    recorder=self.recorder,
                ),
                user_prompt=trajectory_text or "(no trajectory data available)",
                location=self.location,
                token_acc=token_acc,
            )
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[Subagent] Trajectory analyst failed, skipping: {type(e).__name__}: {e}")
            summaries["trajectory"] = "(trajectory analysis unavailable)"
        logger.info("[Subagent] Trajectory analyst complete")

        try:
            summaries["error_log"] = call_subagent(
                model=self.model,
                system_prompt=render_error_log_analyst_prompt(
                    data_description="deploy.log and health_check.log",
                    dspy_config=self.dspy_config,
                    recorder=self.recorder,
                ),
                user_prompt=error_log or "(no error log available)",
                location=self.location,
                token_acc=token_acc,
            )
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[Subagent] Error log analyst failed, skipping: {type(e).__name__}: {e}")
            summaries["error_log"] = "(error log analysis unavailable)"
        logger.info("[Subagent] Error log analyst complete")

        script_input = f"Current script:\n{deploy_script}"
        if original_script:
            script_input += f"\n\nOriginal script (before fixes):\n{original_script}"
        try:
            summaries["script"] = call_subagent(
                model=self.model,
                system_prompt=render_script_analyst_prompt(
                    has_original_script=str(bool(original_script)),
                    dspy_config=self.dspy_config,
                    recorder=self.recorder,
                ),
                user_prompt=script_input or "(no deploy script available)",
                location=self.location,
                token_acc=token_acc,
            )
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[Subagent] Script analyst failed, skipping: {type(e).__name__}: {e}")
            summaries["script"] = "(script analysis unavailable)"
        logger.info("[Subagent] Script analyst complete")

        try:
            summaries["repo"] = call_subagent(
                model=self.model,
                system_prompt=render_repo_analyst_prompt(
                    available_files="Dockerfile, docker-compose, README, code_analysis",
                    dspy_config=self.dspy_config,
                    recorder=self.recorder,
                ),
                user_prompt=repo_context or "(no repository context available)",
                location=self.location,
                token_acc=token_acc,
            )
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[Subagent] Repo analyst failed, skipping: {type(e).__name__}: {e}")
            summaries["repo"] = "(repository analysis unavailable)"
        logger.info("[Subagent] Repo analyst complete")

        if self.recorder and hasattr(self.recorder, "add_assistant_message"):
            for name, summary in summaries.items():
                self.recorder.add_assistant_message(f"[Subagent {name} analyst]\n{summary[:500]}")

        # --- Step 2: Root LLM synthesis --------------------------------------
        logger.info("[Subagent] Starting root synthesis call")
        result = self._root_synthesis(prompt, summaries, deploy_script, token_acc)

        # Write deploy.sh if the root call produced one
        self._write_deploy_sh_if_present(result, repo_path)

        return result

    def _root_synthesis(
        self,
        task_prompt: str,
        summaries: dict[str, str],
        deploy_script: str,
        token_acc: dict[str, int] | None = None,
    ) -> str:
        """Single root LLM call that receives all subagent summaries."""
        import os

        system_msg = render_root_synthesis_prompt(
            num_analysts=str(len(summaries)),
            dspy_config=self.dspy_config,
            recorder=self.recorder,
        )

        user_parts = [
            f"TASK:\n{task_prompt}",
            f"\nCURRENT deploy.sh:\n{deploy_script}" if deploy_script else "",
            f"\n--- Trajectory Analysis ---\n{summaries.get('trajectory', 'N/A')}",
            f"\n--- Error Log Analysis ---\n{summaries.get('error_log', 'N/A')}",
            f"\n--- Script Analysis ---\n{summaries.get('script', 'N/A')}",
            f"\n--- Repository Analysis ---\n{summaries.get('repo', 'N/A')}",
        ]
        user_prompt = "\n".join(p for p in user_parts if p)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            "cache": {"no-cache": True},
        }
        loc = self.location or os.environ.get("VERTEX_LOCATION")
        if loc:
            kwargs["vertex_location"] = loc

        try:
            return litellm_call_with_retry(kwargs, label="root synthesis", token_acc=token_acc)
        except KeyboardInterrupt:
            raise
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.error(f"[Subagent] Root synthesis failed: {type(e).__name__}: {e}")
            return f"Root synthesis failed: {type(e).__name__}: {e}"

    def _write_deploy_sh_if_present(self, response: str, repo_path: Path) -> None:
        """Extract and write deploy.sh from the root synthesis response."""
        file_sections = re.findall(
            r"FILE:\s*(\.sds/deploy\.sh)\s*\n```[^\n]*\n(.*?)```",
            response,
            re.DOTALL,
        )
        if file_sections:
            _, content = file_sections[0]
            out_path = repo_path / ".sds" / "deploy.sh"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            logger.info(f"[Subagent] Wrote {out_path}")

    # -- Helpers --------------------------------------------------------------

    def _read_trajectory(self, sds_dir: Path) -> str:
        """Read and summarise recent trajectory data."""
        traj_path = sds_dir / "trajectory.json"
        if not traj_path.exists():
            return ""
        try:
            data = json.loads(traj_path.read_text())
            # Extract last N deployment conversations
            deployment = data.get("deployment", [])
            last_convos = deployment[-5:] if len(deployment) > 5 else deployment
            return json.dumps(last_convos, indent=2)
        except KeyboardInterrupt:
            raise
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read trajectory from {traj_path}: {e}")
            return ""

    def read_trajectory(self, sds_dir: Path) -> str:
        """Read and summarize recent trajectory data."""
        return self._read_trajectory(sds_dir)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            if path.exists():
                return path.read_text()
        except OSError as e:
            logger.warning(f"Failed to read {path}: {e}")
        return ""

    def read_text(self, path: Path) -> str:
        """Read a text file, returning an empty string on failure."""
        return self._read(path)

    def _gather_repo_context(self, repo_path: Path, sds_dir: Path) -> str:
        """Gather repository-level context files into a single string."""
        parts: list[str] = []

        dockerfile = self._read(repo_path / "Dockerfile")
        if dockerfile:
            parts.append(f"--- Dockerfile ---\n{dockerfile}")

        for name in ("docker-compose.yml", "docker-compose.yaml"):
            compose = self._read(repo_path / name)
            if compose:
                parts.append(f"--- {name} ---\n{compose}")
                break

        for name in ("README.md", "README.rst", "README"):
            readme = self._read(repo_path / name)
            if readme:
                parts.append(f"--- README ---\n{readme}")
                break

        analysis = self._read(sds_dir / "code_analysis.md")
        if analysis:
            parts.append(f"--- Code Analysis ---\n{analysis}")

        return "\n\n".join(parts)

    def gather_repo_context(self, repo_path: Path, sds_dir: Path) -> str:
        """Gather repository-level context files into a single string."""
        return self._gather_repo_context(repo_path, sds_dir)
