"""Hybrid coding agent for the SDS operator.

Combines pre-computed subagent analyses with the RLM REPL loop:

1. Four independent subagents pre-analyse different context slices
   (trajectory, error logs, deploy script, repo) and produce short summaries.
2. The summaries are injected into the RLM REPL namespace alongside the raw
   context variables (error_log, deployment_script, etc.).
3. The root LLM runs the standard RLM loop — it can read the pre-digested
   summaries immediately via ``execute_code``, or drill into the raw data,
   or make further ``recursive_call``s as needed.

Register with ``provider = "hybrid"`` in ``sds.toml``.
"""

from pathlib import Path

from loguru import logger
from app_operator.rlm.environment import RLMContext
from app_operator.rlm.recursive_agent import RecursiveDeploymentAgent
from libs.agent_cli.trajectory import TrajectoryRecorderProtocol
from libs.agent_cli import _FILE_GEN_RE, _DIRECT_TEXT_RE

from libs.agent_cli.base import CodingAgent, register_provider
from libs.agent_cli.events import AgentEventHandler
from libs.agent_cli.subagent import call_subagent
from libs.agent_cli.subagent_agent import (
    SubagentCodingAgent,
    TRAJECTORY_ANALYST_PROMPT,
    ERROR_LOG_ANALYST_PROMPT,
    SCRIPT_ANALYST_PROMPT,
    REPO_ANALYST_PROMPT,
)


@register_provider("hybrid")
class HybridCodingAgent(CodingAgent):
    """Coding agent that pre-populates the RLM REPL with subagent summaries.

    For file-generation and direct-text tasks the behaviour matches
    ``RLMCodingAgent``.  For fix tasks:

    1. Four subagents pre-analyse trajectory, error logs, deploy script, and
       repo context — each returns a short summary (≤ 300 words).
    2. Summaries are stored in ``RLMContext`` as ``trajectory_summary``,
       ``error_summary``, ``script_summary``, and ``repo_summary``.
    3. The full RLM loop runs with those summaries available as REPL
       variables, so the LLM can read them immediately or ignore them and
       drill into the raw data instead.
    """

    def __init__(
        self,
        model: str | None = None,
        recorder: TrajectoryRecorderProtocol | None = None,
        event_handler: AgentEventHandler | None = None,
        location: str | None = None,
    ):
        self.model = model or "vertex_ai/gemini-2.0-flash"
        self.recorder = recorder
        self.event_handler = event_handler
        self.location = location
        self._total_token_usage: dict = {
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
        call_tokens: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if _DIRECT_TEXT_RE.search(prompt):
            # Reuse SubagentCodingAgent's direct-text path (same implementation)
            helper = SubagentCodingAgent(model=self.model, location=self.location)
            result = helper._generate_direct(prompt, call_tokens)
        elif _FILE_GEN_RE.search(prompt):
            helper = SubagentCodingAgent(model=self.model, location=self.location)
            result = helper._generate_files(prompt, repo_path, call_tokens)
        else:
            result = self._generate_fix(prompt, repo_path, call_tokens)

        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._total_token_usage[k] += call_tokens[k]

        if self.recorder and hasattr(self.recorder, "record_token_usage"):
            self.recorder.record_token_usage(self._total_token_usage.copy())

        return result

    def _generate_fix(
        self,
        prompt: str,
        repo_path: Path,
        token_acc: dict | None = None,
    ) -> str:
        """Pre-run 4 subagent analyses, then hand off to the RLM loop."""
        helper = SubagentCodingAgent(model=self.model, location=self.location)
        sds = repo_path / ".sds"

        trajectory_text = helper._read_trajectory(sds)
        deploy_log = helper._read(sds / "logs" / "deploy.log")
        health_check_log = helper._read(sds / "logs" / "health_check.log")
        error_log = deploy_log + "\n" + health_check_log
        deploy_script = helper._read(sds / "deploy.sh")
        original_script = helper._read(sds / "deploy.sh.bak")
        repo_context = helper._gather_repo_context(repo_path, sds)

        logger.info("[Hybrid] Pre-running 4 subagent analyses")

        try:
            trajectory_summary = call_subagent(
                model=self.model,
                system_prompt=TRAJECTORY_ANALYST_PROMPT,
                user_prompt=trajectory_text or "(no trajectory data available)",
                location=self.location,
                token_acc=token_acc,
            )
        except Exception as e:
            logger.warning(f"[Hybrid] Trajectory analyst failed, skipping: {e}")
            trajectory_summary = "(trajectory analysis unavailable)"
        logger.info("[Hybrid] Trajectory analyst complete")

        try:
            error_summary = call_subagent(
                model=self.model,
                system_prompt=ERROR_LOG_ANALYST_PROMPT,
                user_prompt=error_log or "(no error log available)",
                location=self.location,
                token_acc=token_acc,
            )
        except Exception as e:
            logger.warning(f"[Hybrid] Error log analyst failed, skipping: {e}")
            error_summary = "(error log analysis unavailable)"
        logger.info("[Hybrid] Error log analyst complete")

        script_input = f"Current script:\n{deploy_script}"
        if original_script:
            script_input += f"\n\nOriginal script (before fixes):\n{original_script}"
        try:
            script_summary = call_subagent(
                model=self.model,
                system_prompt=SCRIPT_ANALYST_PROMPT,
                user_prompt=script_input or "(no deploy script available)",
                location=self.location,
                token_acc=token_acc,
            )
        except Exception as e:
            logger.warning(f"[Hybrid] Script analyst failed, skipping: {e}")
            script_summary = "(script analysis unavailable)"
        logger.info("[Hybrid] Script analyst complete")

        try:
            repo_summary = call_subagent(
                model=self.model,
                system_prompt=REPO_ANALYST_PROMPT,
                user_prompt=repo_context or "(no repository context available)",
                location=self.location,
                token_acc=token_acc,
            )
        except Exception as e:
            logger.warning(f"[Hybrid] Repo analyst failed, skipping: {e}")
            repo_summary = "(repository analysis unavailable)"
        logger.info("[Hybrid] Repo analyst complete")

        if self.recorder and hasattr(self.recorder, "add_assistant_message"):
            for name, summary in [
                ("trajectory", trajectory_summary),
                ("error_log", error_summary),
                ("script", script_summary),
                ("repo", repo_summary),
            ]:
                self.recorder.add_assistant_message(
                    f"[Hybrid pre-analysis: {name}]\n{summary[:500]}"
                )

        # Build the backup before creating context (same as RLMCodingAgent)
        deploy_sh = sds / "deploy.sh"
        backup = sds / "deploy.sh.bak"
        if deploy_sh.exists() and not backup.exists():
            try:
                backup.write_text(deploy_script)
                logger.info(f"[Hybrid] Saved deploy.sh backup: {backup}")
            except Exception as e:
                logger.warning(f"[Hybrid] Could not save deploy.sh backup: {e}")

        deploy_log = helper._read(sds / "logs" / "deploy.log")
        health_check_log = helper._read(sds / "logs" / "health_check.log")
        context = RLMContext(
            error_log=deploy_log,
            deployment_script=deploy_script,
            health_check_output=health_check_log,
            dockerfile=helper._read(repo_path / "Dockerfile"),
            docker_compose=(
                helper._read(repo_path / "docker-compose.yml")
                or helper._read(repo_path / "docker-compose.yaml")
            ),
            readme=(
                helper._read(repo_path / "README.md")
                or helper._read(repo_path / "README.rst")
            ),
            analysis_report=helper._read(sds / "code_analysis.md"),
            original_script=original_script,
            # Pre-computed summaries injected into the REPL namespace
            trajectory_summary=trajectory_summary,
            error_summary=error_summary,
            script_summary=script_summary,
            repo_summary=repo_summary,
        )

        logger.info("[Hybrid] Starting RLM loop with pre-populated summaries")
        agent = RecursiveDeploymentAgent(
            trajectory=self.recorder,
            max_recursion_depth=5,
            llm_provider=self.model,
            vertex_location=self.location,
        )
        result = agent.run_task(task=prompt, context=context, repo_path=str(repo_path))

        rlm_tokens = agent.get_rlm_statistics().get("token_usage", {})
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if token_acc is not None:
                token_acc[k] = token_acc.get(k, 0) + rlm_tokens.get(k, 0)

        return result
