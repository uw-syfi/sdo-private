"""Hybrid coding agent for the SDS operator.

Combines the RLM REPL loop with lazy specialist analyses:

1. The root LLM starts in the standard RLM loop immediately.
2. When the root LLM wants focused help, it can request one of four
   specialists (trajectory, error logs, deploy script, repo).
3. Specialist summaries are cached into the RLM namespace so later steps can
   read them via ``execute_code`` without paying for the same analysis twice.

Register with ``provider = "hybrid"`` in ``sds.toml``.
"""

import subprocess
from pathlib import Path
from typing import Any

from agentshim import BaseCodingAgent
from agentshim.base import register_provider
from agentshim.events import AgentEventHandler
from agentshim.trajectory import NullTrajectoryRecorder, TrajectoryRecorderProtocol
from loguru import logger

from app_operator.cli_agent._rlm_utils import DIRECT_TEXT_RE, FILE_GEN_RE, FIX_ERROR_RE
from app_operator.cli_agent._subagent_utils import call_subagent
from app_operator.cli_agent.rlm.environment import RLMContext
from app_operator.cli_agent.rlm.recursive_agent import RecursiveDeploymentAgent
from app_operator.cli_agent.subagent_agent import SubagentCodingAgent
from app_operator.prompts import (
    DSPyConfigProtocol,
    render_error_log_analyst_prompt,
    render_fix_error_task_prompt,
    render_repo_analyst_prompt,
    render_script_analyst_prompt,
    render_trajectory_analyst_prompt,
)


@register_provider("hybrid")
class HybridCodingAgent(BaseCodingAgent):
    """Coding agent that exposes lazy specialists to the RLM loop.

    For file-generation and direct-text tasks the behaviour matches
    ``RLMCodingAgent``.  For fix tasks:

    1. The full RLM loop starts with raw context only.
    2. The root LLM can request focused specialist analyses on demand.
    3. Returned summaries are stored in ``RLMContext`` as cached summary
       variables for later REPL access.
    """

    _SPECIALISTS: dict[str, str] = {
        "trajectory": "Summarize what has already been tried and recurring failure patterns.",
        "error_log": "Analyze deploy.log and health_check.log to isolate likely root causes.",
        "script": "Review deploy.sh and any backup to identify likely script issues or regressions.",
        "repo": "Summarize deployment-relevant repository constraints from Dockerfile, compose, README, and analysis.",
    }

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        location: str | None = None,
        dspy_config: DSPyConfigProtocol | None = None,
        rlm_mode: str = "compatibility",
    ):
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder: TrajectoryRecorderProtocol = recorder or NullTrajectoryRecorder()
        self.event_handler = event_handler
        self.location = location
        self.dspy_config = dspy_config
        self.rlm_mode = rlm_mode
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
            # Reuse SubagentCodingAgent's direct-text path (same implementation)
            helper = SubagentCodingAgent(model=self.model, location=self.location, dspy_config=self.dspy_config)
            result = helper.generate_direct(prompt, call_tokens)
        elif not is_fix and FILE_GEN_RE.search(prompt):
            helper = SubagentCodingAgent(model=self.model, location=self.location, dspy_config=self.dspy_config)
            result = helper.generate_files(prompt, repo_path, call_tokens)
        else:
            result = self._generate_fix(prompt, repo_path, call_tokens)

        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._total_token_usage[k] += call_tokens[k]

        if self.recorder and hasattr(self.recorder, "record_token_usage"):
            self.recorder.record_token_usage(self._total_token_usage.copy())  # type: ignore[reportArgumentType]

        return result

    def _generate_fix(
        self,
        prompt: str,
        repo_path: Path,
        token_acc: dict[str, int] | None = None,
    ) -> str:
        """Start the RLM loop and let it call specialists lazily."""
        helper = SubagentCodingAgent(model=self.model, location=self.location, dspy_config=self.dspy_config)
        sds = repo_path / ".sds"
        deploy_script = helper.read_text(sds / "deploy.sh")
        original_script = helper.read_text(sds / "deploy.sh.bak")

        # Build the backup before creating context (same as RLMCodingAgent)
        deploy_sh = sds / "deploy.sh"
        backup = sds / "deploy.sh.bak"
        if deploy_sh.exists() and not backup.exists():
            try:
                backup.write_text(deploy_script)
                logger.info(f"[Hybrid] Saved deploy.sh backup: {backup}")
            except OSError as e:
                logger.warning(f"[Hybrid] Could not save deploy.sh backup: {e}")

        deploy_log = helper.read_text(sds / "logs" / "deploy.log")
        health_check_log = helper.read_text(sds / "logs" / "health_check.log")
        context = RLMContext(
            error_log=deploy_log,
            deployment_script=deploy_script,
            health_check_output=health_check_log,
            dockerfile=helper.read_text(repo_path / "Dockerfile"),
            docker_compose=(
                helper.read_text(repo_path / "docker-compose.yml")
                or helper.read_text(repo_path / "docker-compose.yaml")
            ),
            readme=(helper.read_text(repo_path / "README.md") or helper.read_text(repo_path / "README.rst")),
            analysis_report=helper.read_text(sds / "code_analysis.md"),
            original_script=original_script,
        )

        logger.info("[Hybrid] Starting RLM loop with lazy specialist delegation")
        agent = RecursiveDeploymentAgent(
            trajectory=self.recorder,
            max_recursion_depth=5,
            llm_provider=self.model,
            vertex_location=self.location,
            rlm_mode=self.rlm_mode,
            dspy_config=self.dspy_config,
            specialist_dispatcher=lambda specialist, task: self.run_specialist_analysis(
                helper=helper,
                repo_path=repo_path,
                specialist=specialist,
                task=task,
                token_acc=token_acc,
            ),
            available_specialists=self.available_specialists,
        )
        if self.rlm_mode == "paper_faithful":
            rlm_task = prompt
        else:
            # Use the RLM-specific task prompt instead of the standard deployer_fix_error
            # output. The 270-line non-RLM prompt was written for agents that receive logs
            # as raw text; the RLM handles context management natively via REPL variables,
            # so a simpler RLM-specific prompt is more appropriate and can be GEPA-optimised
            # independently from the non-RLM deployer_fix_error prompt.
            rlm_task = render_fix_error_task_prompt(
                available_specialists=", ".join(sorted(self._SPECIALISTS)),
                dspy_config=self.dspy_config,
                recorder=self.recorder if hasattr(self.recorder, "record_prompt_kwargs") else None,
            )
        result = agent.run_task(task=rlm_task, context=context, repo_path=str(repo_path))

        rlm_tokens: dict[str, Any] = agent.get_rlm_statistics().get("token_usage", {})
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if token_acc is not None:
                token_acc[k] = token_acc.get(k, 0) + rlm_tokens.get(k, 0)

        return result

    def _run_specialist_analysis(
        self,
        helper: SubagentCodingAgent,
        repo_path: Path,
        specialist: str,
        task: str,
        token_acc: dict[str, int] | None = None,
    ) -> str:
        """Run one specialist analysis against the latest repo state."""
        sds = repo_path / ".sds"
        task_prefix = f"Focused task: {task}\n\n" if task else ""

        try:
            if specialist == "trajectory":
                summary = call_subagent(
                    model=self.model,
                    system_prompt=render_trajectory_analyst_prompt(
                        data_description="deployment trajectory JSON",
                        dspy_config=self.dspy_config,
                        recorder=self.recorder,
                    ),
                    user_prompt=task_prefix + (helper.read_trajectory(sds) or "(no trajectory data available)"),
                    location=self.location,
                    token_acc=token_acc,
                )
            elif specialist == "error_log":
                error_log = (
                    helper.read_text(sds / "logs" / "deploy.log")
                    + "\n"
                    + helper.read_text(sds / "logs" / "health_check.log")
                )
                summary = call_subagent(
                    model=self.model,
                    system_prompt=render_error_log_analyst_prompt(
                        data_description="deploy.log and health_check.log",
                        dspy_config=self.dspy_config,
                        recorder=self.recorder,
                    ),
                    user_prompt=task_prefix + (error_log or "(no error log available)"),
                    location=self.location,
                    token_acc=token_acc,
                )
            elif specialist == "script":
                deploy_script = helper.read_text(sds / "deploy.sh")
                original_script = helper.read_text(sds / "deploy.sh.bak")
                script_input = f"{task_prefix}Current script:\n{deploy_script}"
                if original_script:
                    script_input += f"\n\nOriginal script (before fixes):\n{original_script}"
                summary = call_subagent(
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
            elif specialist == "repo":
                summary = call_subagent(
                    model=self.model,
                    system_prompt=render_repo_analyst_prompt(
                        available_files="Dockerfile, docker-compose, README, code_analysis",
                        dspy_config=self.dspy_config,
                        recorder=self.recorder,
                    ),
                    user_prompt=task_prefix
                    + (helper.gather_repo_context(repo_path, sds) or "(no repository context available)"),
                    location=self.location,
                    token_acc=token_acc,
                )
            else:
                summary = f"(unknown specialist: {specialist})"
        except (TimeoutError, ConnectionError, subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[Hybrid] {specialist} specialist failed, skipping: {e}")
            summary = f"({specialist} analysis unavailable)"

        if self.recorder and hasattr(self.recorder, "add_assistant_message"):
            self.recorder.add_assistant_message(f"[Hybrid specialist: {specialist}]\n{summary[:500]}")

        logger.info(f"[Hybrid] {specialist} specialist complete")
        return summary

    @property
    def available_specialists(self) -> dict[str, str]:
        """Specialists exposed to external chat orchestration."""
        return self._SPECIALISTS if self.rlm_mode == "compatibility" else {}

    def run_specialist_analysis(
        self,
        helper: SubagentCodingAgent,
        repo_path: Path,
        specialist: str,
        task: str,
        token_acc: dict[str, int] | None = None,
    ) -> str:
        """Public wrapper for specialist analysis dispatch."""
        return self._run_specialist_analysis(helper, repo_path, specialist, task, token_acc)
