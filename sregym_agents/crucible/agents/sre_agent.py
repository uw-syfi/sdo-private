"""SREAgent — encapsulates the SRE agent role: tool assembly, prompt
rendering, deps construction, shared-file writing, and run_subagent creation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
    from sregym_agents.crucible.tools._deps import SharedFile, SharedState, SRESubmission
    from sregym_agents.crucible.tools._kb_tools import TriagePriors

logger = logging.getLogger(__name__)


@dataclass
class SREAgentConfig:
    """Static configuration that doesn't change per-iteration."""

    trajectory_path: Path | None = None
    enable_ltm_retrieval: bool = False
    enable_ltm_verified_direct_submit: bool = False
    lt_summary_file: Path | None = None
    incidents_dir: Path | None = None
    playbooks_dir: Path | None = None
    mitigation_playbooks_dir: Path | None = None
    triage_priors: TriagePriors | None = None
    verification_guidance: str = ""
    stage_outputs_file: Path | None = None


class SREAgent:
    """Encapsulates the SRE agent role: tool assembly, prompt rendering,
    deps construction, shared-file writing, and run_subagent creation.

    The orchestrator calls ``run()`` and gets back an ``AgentResult`` with
    typed ``SRESubmission`` output (or ``None`` on failure/interrupt).
    """

    def __init__(
        self,
        driver: AgentDriver,
        model_id: Any,
        renderer: PromptRenderer,
        config: SREAgentConfig | None = None,
    ) -> None:
        self._driver = driver
        self._model_id = model_id
        self._renderer = renderer
        self._config = config or SREAgentConfig()

    def _assemble_tools(self, stage: str) -> list[Any]:
        """Return the tool list for the given stage."""
        from sregym_agents.crucible.tools import (
            check_hypothesis_coverage,
            exec_bash,
            grep,
            read_file,
            search_prior_incidents,
            search_prior_mitigations,
            str_replace_file,
            triage_cluster,
            write_file,
        )

        tools: list[Any] = [exec_bash, read_file, grep, write_file, str_replace_file]
        if stage != "mitigation":
            tools.append(triage_cluster)
        tools.append(search_prior_incidents if stage == "diagnosis" else search_prior_mitigations)
        if stage != "mitigation":
            tools.append(check_hypothesis_coverage)
        return tools

    @property
    def config(self) -> SREAgentConfig:
        """Public access to the agent's static configuration."""
        return self._config

    def make_run_subagent(self, usage_collector: UsageCollector | None = None) -> Any:
        """Create a ``run_subagent`` closure over ``self._driver``."""
        driver = self._driver

        async def _run_subagent(
            *,
            prompt: str,
            output_type: type,
            tools: list[Any] | None = None,
            agent_name: str = "",
            model_settings: dict[str, Any] | None = None,
            usage_collector: UsageCollector | None = usage_collector,
        ) -> Any:
            result = await driver.run(  # pyright: ignore[reportUnknownVariableType]
                prompt=prompt,
                output_type=output_type,
                tools=tools,
                agent_name=agent_name,
                model_settings=model_settings,
                usage_collector=usage_collector,
            )
            return result.unwrap(agent_name)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]

        return _run_subagent

    def _build_deps(
        self,
        *,
        namespace: str,
        shared_file: SharedFile,
        iteration: int,
        stage: str,
        state: SharedState,
        usage_collector: UsageCollector | None = None,
    ) -> Any:
        """Construct ``SREDeps`` for this run."""
        from sregym_agents.crucible.tools import SREDeps

        cfg = self._config
        return SREDeps(
            namespace=namespace,
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            model_id=self._model_id,
            renderer=self._renderer,
            state=state,
            lt_summary_file=cfg.lt_summary_file if cfg.enable_ltm_retrieval else None,
            incidents_dir=cfg.incidents_dir if cfg.enable_ltm_retrieval else None,
            playbooks_dir=cfg.playbooks_dir if cfg.enable_ltm_retrieval else None,
            mitigation_playbooks_dir=(cfg.mitigation_playbooks_dir if cfg.enable_ltm_retrieval else None),
            enable_ltm_verified_direct_submit=cfg.enable_ltm_verified_direct_submit,
            trajectory_path=cfg.trajectory_path,
            triage_priors=cfg.triage_priors,
            verification_guidance=cfg.verification_guidance,
            stage_outputs_file=cfg.stage_outputs_file,
            usage_collector=usage_collector,
            run_subagent=self.make_run_subagent(usage_collector),
        )

    def _render_prompts(
        self,
        stage: str,
        *,
        app_info: dict[str, Any],
        iteration: int,
        shared_content: str,
        shared_file_str: str,
        architecture_content: str = "",
        lt_summary_content: str = "",
        lessons_content: str = "",
        lt_summary_file: str = "",
    ) -> tuple[str, str]:
        """Render system and user prompts for the SRE agent."""
        system_prompt = self._renderer.render(f"{stage}_agent_system")
        user_prompt = self._renderer.render(
            f"{stage}_agent_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=shared_file_str,
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
            lt_summary_file=lt_summary_file,
        )
        return system_prompt, user_prompt

    def _model_settings(self) -> dict[str, Any]:
        """Return model_settings for SRE agent runs."""
        from libs.pydantic_agent import thinking_settings
        from sregym_agents.crucible.tools import MAX_OUTPUT_TOKENS, THINKING_BUDGET

        settings = thinking_settings(self._model_id, THINKING_BUDGET)
        return {**settings, "max_tokens": MAX_OUTPUT_TOKENS}

    async def run(
        self,
        *,
        app_info: dict[str, Any],
        stage: str,
        iteration: int,
        shared_file: SharedFile,
        shared_content: str,
        architecture_content: str = "",
        lt_summary_content: str = "",
        lessons_content: str = "",
        lt_summary_file: str = "",
        usage_collector: UsageCollector | None = None,
        system_prompt_override: str | None = None,
    ) -> AgentResult[SRESubmission]:
        """Run the SRE agent for one iteration.

        Returns an ``AgentResult`` — the orchestrator checks ``completed``
        and ``interrupt_data`` to decide on short-circuit or continuation.

        Post-run side effects (writing to shared_file, updating state)
        are handled via deps and tools.  This method delegates fully to
        ``driver.run()``.
        """
        from sregym_agents.crucible.tools import SharedState, SRESubmission

        state = SharedState()
        deps = self._build_deps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            state=state,
            usage_collector=usage_collector,
        )

        system_prompt, user_prompt = self._render_prompts(
            stage,
            app_info=app_info,
            iteration=iteration,
            shared_content=shared_content,
            shared_file_str=str(shared_file),
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
            lt_summary_file=lt_summary_file,
        )

        if system_prompt_override:
            system_prompt = system_prompt_override

        logger.info(f"[{stage}-agent] SYSTEM PROMPT:\n{system_prompt}")
        logger.info(f"[{stage}-agent] USER PROMPT:\n{user_prompt}")

        tools = self._assemble_tools(stage)

        result: AgentResult[SRESubmission] = await self._driver.run(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=tools,
            output_type=SRESubmission,
            agent_name=f"sre-{stage}",
            model_settings=self._model_settings(),
            usage_collector=usage_collector,
            deps=deps,
            run_ctx={"stage": stage, "iteration": iteration, "role": "sre"},
        )

        # Post-run: extract state from deps (tools update state directly)
        if result.completed and result.output is not None:
            output = result.output
            state.submitted = True
            state.answer = output.answer
            state.answer_justification = output.justification
            state.answer_causal_chain = output.causal_chain
            state.answer_reflection = output.reflection

            # Write iteration entry to shared file
            if stage == "diagnosis":
                entry = f"\n### Iteration {iteration} — Agent Hypothesis\n[Submitted — pending judge review]\n"
            else:
                entry = (
                    f"\n### Iteration {iteration} — Agent Strategy\n"
                    f"**Mitigation**: {output.answer}\n"
                    f"**Justification**: {output.justification}\n"
                )
            try:
                shared_file.append(entry)
            except Exception as e:
                logger.warning(f"Error writing to shared file: {e}")

        # Attach state and last_run_messages to result for orchestrator access
        result.state = state  # type: ignore[attr-defined]
        return result
