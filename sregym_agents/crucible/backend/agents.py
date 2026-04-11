"""Role agents — high-level classes that encapsulate tool assembly, prompt
rendering, deps construction, and role-specific behavior.

Each role agent owns everything about its domain: which tools to register,
how to render prompts, how to build deps, and how to handle role-specific
post-run logic.  The orchestrator becomes a pure loop controller that
delegates to these classes.

All three classes accept an ``AgentDriver`` and call ``driver.run()`` —
the orchestrator never touches pydantic-ai directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.backend.base import AgentDriver, AgentResult
    from sregym_agents.crucible.recovery_reflection import RecoveryReflection
    from sregym_agents.crucible.tools._deps import SharedFile, SharedState, SRESubmission
    from sregym_agents.crucible.tools._kb_tools import TriagePriors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SREAgent
# ---------------------------------------------------------------------------


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
            if result.output is None:  # pyright: ignore[reportUnknownMemberType]
                raise RuntimeError(f"Subagent {agent_name} produced no output")
            return result.output  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]

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


# ---------------------------------------------------------------------------
# JudgeAgent
# ---------------------------------------------------------------------------


class JudgeAgent:
    """Encapsulates the judge agent role: tool assembly, prompt rendering,
    deps construction, and the submit-reminder retry loop.
    """

    MAX_SUBMIT_REMINDERS = 3

    def __init__(
        self,
        driver: AgentDriver,
        model_id: Any,
        renderer: PromptRenderer,
        trajectory_path: Path | None = None,
    ) -> None:
        self._driver = driver
        self._model_id = model_id
        self._renderer = renderer
        self._trajectory_path = trajectory_path

    def _assemble_tools(self) -> list[Any]:
        """Return the tool list for the judge."""
        from sregym_agents.crucible.tools import (
            exec_bash_any,
            grep,
            read_file,
            reveal_agent_hypothesis,
            str_replace_file,
            submit_independent_findings,
            submit_verdict,
            write_file,
        )

        return [
            exec_bash_any,
            read_file,
            grep,
            write_file,
            str_replace_file,
            submit_independent_findings,
            reveal_agent_hypothesis,
            submit_verdict,
        ]

    def _model_settings(self) -> dict[str, Any]:
        """Return model_settings for judge runs."""
        from libs.pydantic_agent import thinking_settings
        from sregym_agents.crucible.tools import THINKING_BUDGET

        return dict(thinking_settings(self._model_id, THINKING_BUDGET))

    async def run(
        self,
        *,
        app_info: dict[str, Any],
        stage: str,
        iteration: int,
        shared_file: SharedFile,
        shared_content: str,
        submit_mcp_url: str,
        hypothesis_text: str = "",
        architecture_content: str = "",
        lt_summary_content: str = "",
        lessons_content: str = "",
        usage_collector: UsageCollector | None = None,
    ) -> AgentResult[str]:
        """Run the judge agent with submit-reminder retry loop.

        Checks ``deps.state.submitted`` even on ``completed=False`` — the
        judge may have called ``submit_verdict`` before the model error.
        """
        from sregym_agents.crucible.tools import JudgeDeps, SharedState

        state = SharedState()
        deps = JudgeDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=iteration,
            stage=stage,
            submit_mcp_url=submit_mcp_url,
            renderer=self._renderer,
            hypothesis_text=hypothesis_text,
            state=state,
            usage_collector=usage_collector,
        )

        system_prompt = self._renderer.render(f"{stage}_judge_system")
        user_prompt = self._renderer.render(
            f"{stage}_judge_user",
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            iteration=iteration,
            shared_content=shared_content,
            shared_file=str(shared_file),
            architecture_content=architecture_content,
            lt_summary_content=lt_summary_content,
            lessons_content=lessons_content,
        )
        logger.info(f"[{stage}-judge] SYSTEM PROMPT:\n{system_prompt}")
        logger.info(f"[{stage}-judge] USER PROMPT:\n{user_prompt}")

        tools = self._assemble_tools()
        model_settings = self._model_settings()

        message_history: list[Any] | None = None
        current_prompt = user_prompt
        reminder_count = 0
        last_result: AgentResult[str] | None = None

        while True:
            result: AgentResult[str] = await self._driver.run(
                prompt=current_prompt,
                system_prompt=system_prompt,
                tools=tools,
                output_type=str,
                agent_name=f"judge-{stage}",
                model_settings=model_settings,
                message_history=message_history,
                usage_collector=usage_collector,
                deps=deps,
                run_ctx={"stage": stage, "iteration": iteration, "role": "judge"},
            )
            last_result = result

            if not result.completed:
                # Check if judge submitted before the error
                if state.submitted:
                    logger.warning("Judge model error after submitting; treating as complete.")
                else:
                    logger.warning("Judge model error without submitting; treating as unsubmitted.")
                break

            if state.submitted:
                break

            if reminder_count >= self.MAX_SUBMIT_REMINDERS:
                logger.warning("Max judge submit reminders reached — giving up.")
                break

            reminder_count += 1
            logger.warning(f"Judge stopped without submitting (reminder {reminder_count}/{self.MAX_SUBMIT_REMINDERS}).")
            message_history = result.messages
            current_prompt = (
                "You have not submitted your verdict yet. "
                "Please call `submit_verdict` with your final verdict before finishing."
            )

        # Attach state for orchestrator access
        assert last_result is not None  # loop always runs at least once
        last_result.state = state  # type: ignore[attr-defined]
        return last_result


# ---------------------------------------------------------------------------
# RecoveryAgent
# ---------------------------------------------------------------------------


class RecoveryAgent:
    """Encapsulates recovery-phase behavior: diagnosis recovery, reflection,
    and mitigation recovery.

    Each method renders its own prompts, calls ``driver.run()``, and writes
    results to shared_file / stage_outputs_file.
    """

    def __init__(
        self,
        driver: AgentDriver,
        model_id: Any,
        renderer: PromptRenderer,
        trajectory_path: Path | None = None,
    ) -> None:
        self._driver = driver
        self._model_id = model_id
        self._renderer = renderer
        self._trajectory_path = trajectory_path

    def _sre_tools(self) -> list[Any]:
        from sregym_agents.crucible.tools import (
            exec_bash,
            grep,
            read_file,
            str_replace_file,
            write_file,
        )

        return [exec_bash, read_file, grep, write_file, str_replace_file]

    def _sre_model_settings(self) -> dict[str, Any]:
        from libs.pydantic_agent import thinking_settings
        from sregym_agents.crucible.tools import MAX_OUTPUT_TOKENS, THINKING_BUDGET

        settings = thinking_settings(self._model_id, THINKING_BUDGET)
        return {**settings, "max_tokens": MAX_OUTPUT_TOKENS}

    async def run_diagnosis(
        self,
        *,
        app_info: dict[str, Any],
        shared_file: SharedFile,
        original_answer: str,
        benchmark_block: str,
        usage_collector: UsageCollector | None = None,
        original_justification: str = "",
        original_causal_chain: str = "",
        stage_outputs_file: Path | None = None,
    ) -> SRESubmission | None:
        """Run a recovery diagnosis agent to produce a causal chain for the correct root cause."""

        from sregym_agents.crucible.tools import SharedState, SREDeps, SRESubmission

        reasoning = self._extract_benchmark_reasoning(benchmark_block)
        if not reasoning:
            logger.warning("Recovery diagnosis: no benchmark reasoning found, skipping.")
            return None

        logger.info("=" * 60)
        logger.info("RECOVERY DIAGNOSIS: producing causal chain from benchmark ground truth")
        logger.info("=" * 60)

        if stage_outputs_file:
            with open(stage_outputs_file, "a") as f:
                f.write("\n---\n## Recovery Diagnosis Investigation\n")

        state = SharedState()
        deps = SREDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=0,
            stage="diagnosis",
            model_id=self._model_id,
            renderer=self._renderer,
            state=state,
            stage_outputs_file=stage_outputs_file,
            usage_collector=usage_collector,
        )

        system_prompt = self._renderer.render("recovery_diagnosis_system")
        user_prompt = self._renderer.render(
            "recovery_diagnosis_user",
            benchmark_reasoning=reasoning,
            original_answer=original_answer,
            original_justification=original_justification,
            original_causal_chain=original_causal_chain,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
        )
        logger.info(f"[recovery-diagnosis] SYSTEM PROMPT:\n{system_prompt}")
        logger.info(f"[recovery-diagnosis] USER PROMPT:\n{user_prompt}")

        result = await self._driver.run(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=self._sre_tools(),
            output_type=SRESubmission,
            agent_name="recovery-diagnosis",
            model_settings=self._sre_model_settings(),
            usage_collector=usage_collector,
            deps=deps,
            run_ctx={"stage": "diagnosis", "iteration": 0, "role": "recovery"},
        )

        if not result.completed or result.output is None:
            # Check if the agent submitted via deps state
            if not state.submitted:
                logger.warning("Recovery diagnosis agent did not submit an answer.")
                return None
            # Build submission from state
            submission = SRESubmission(
                answer=state.answer or "",
                justification=state.answer_justification or "",
                causal_chain=state.answer_causal_chain or "",
                reflection=state.answer_reflection or "",
            )
        else:
            # Update state from output
            state.submitted = True
            state.answer = result.output.answer
            state.answer_justification = result.output.justification
            state.answer_causal_chain = result.output.causal_chain
            state.answer_reflection = result.output.reflection
            submission = SRESubmission(
                answer=result.output.answer,
                justification=result.output.justification,
                causal_chain=result.output.causal_chain,
                reflection=result.output.reflection,
                message_history=result.messages,
            )

        # Append recovery result to shared file
        entry = (
            f"\n### Recovery Diagnosis\n"
            f"**Diagnosis**: {submission.answer}\n"
            f"**Justification**: {submission.justification}\n"
        )
        if submission.causal_chain:
            entry += f"**Causal Chain**: {submission.causal_chain}\n"
        if submission.reflection:
            entry += f"**Agent Reflection**: {submission.reflection}\n"
        try:
            shared_file.append(entry)
        except Exception as e:
            logger.warning(f"Error writing recovery diagnosis to shared file: {e}")

        # Append to stage outputs file
        if stage_outputs_file:
            with open(stage_outputs_file, "a") as f:
                f.write(f"**Diagnosis**: {submission.answer}\n")
                f.write(f"**Justification**: {submission.justification}\n")
                if submission.causal_chain:
                    f.write(f"**Causal Chain**: {submission.causal_chain}\n")
                if submission.reflection:
                    f.write(f"**Agent Reflection**: {submission.reflection}\n")

        logger.info(f"Recovery diagnosis complete: {submission.answer}")
        return submission

    async def run_reflection(
        self,
        *,
        app_info: dict[str, Any],
        original_answer: str,
        original_justification: str = "",
        original_causal_chain: str = "",
        stage_outputs_file: Path | None = None,
        phase1_messages: list[Any] | None = None,
        usage_collector: UsageCollector | None = None,
    ) -> RecoveryReflection:
        """Produce a KB-focused reflection from grounded recovery context."""
        from sregym_agents.crucible.recovery_reflection import RecoveryReflection

        stage_outputs = ""
        if stage_outputs_file and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text().strip()

        system_prompt = self._renderer.render("recovery_reflection_system")
        user_prompt = self._renderer.render(
            "recovery_reflection_user",
            stage_outputs=stage_outputs or "(No stage outputs captured.)",
            original_answer=original_answer,
            original_justification=original_justification,
            original_causal_chain=original_causal_chain,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
        )

        result = await self._driver.run(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=None,
            output_type=RecoveryReflection,
            agent_name="recovery-reflection",
            usage_collector=usage_collector,
            message_history=phase1_messages,
        )

        if result.output is None:
            logger.warning("Recovery reflection produced no output; returning empty reflection.")
            return RecoveryReflection(summary="Recovery reflection failed to produce output.")

        reflection = result.output

        # Write to stage outputs file
        if stage_outputs_file:
            with open(stage_outputs_file, "a") as f:
                f.write("\n---\n## Recovery Reflection\n")
                f.write(f"**Summary**: {reflection.summary}\n")
                if reflection.investigation_observations:
                    f.write("**Grounded Observations**:\n")
                    f.writelines(f"- {obs}\n" for obs in reflection.investigation_observations)
                for failure in reflection.stage_failures:
                    f.write(f"### {failure.stage}\n")
                    f.write(f"**Description**: {failure.description}\n")
                    f.write(f"**Evidence**: {failure.evidence}\n")
                    f.write(f"**Lesson**: {failure.lesson}\n")

        return reflection

    async def run_mitigation(
        self,
        *,
        app_info: dict[str, Any],
        shared_file: SharedFile,
        original_answer: str,
        benchmark_block: str,
        usage_collector: UsageCollector | None = None,
        original_justification: str = "",
        diagnosis_answer: str = "",
        stage_outputs_file: Path | None = None,
    ) -> SRESubmission | None:
        """Run a recovery mitigation agent to investigate and apply the correct fix."""
        from sregym_agents.crucible.tools import SharedState, SREDeps, SRESubmission

        reasoning = self._extract_benchmark_reasoning(benchmark_block, stage="mitigation")
        if not reasoning:
            logger.warning("Recovery mitigation: no benchmark reasoning found, skipping.")
            return None

        logger.info("=" * 60)
        logger.info("RECOVERY MITIGATION: reflecting on failed mitigation attempt")
        logger.info("=" * 60)

        if stage_outputs_file:
            with open(stage_outputs_file, "a") as f:
                f.write("\n---\n## Recovery Mitigation Investigation\n")

        state = SharedState()
        deps = SREDeps(
            namespace=app_info.get("namespace", "default"),
            shared_file=shared_file,
            iteration=0,
            stage="mitigation",
            model_id=self._model_id,
            renderer=self._renderer,
            state=state,
            stage_outputs_file=stage_outputs_file,
            usage_collector=usage_collector,
        )

        system_prompt = self._renderer.render("recovery_mitigation_system")
        user_prompt = self._renderer.render(
            "recovery_mitigation_user",
            benchmark_reasoning=reasoning,
            original_answer=original_answer,
            original_justification=original_justification,
            diagnosis_answer=diagnosis_answer,
            app_name=app_info.get("app_name", "unknown"),
            namespace=app_info.get("namespace", "default"),
            descriptions=app_info.get("descriptions", ""),
        )
        logger.info(f"[recovery-mitigation] SYSTEM PROMPT:\n{system_prompt}")
        logger.info(f"[recovery-mitigation] USER PROMPT:\n{user_prompt}")

        result = await self._driver.run(
            prompt=user_prompt,
            system_prompt=system_prompt,
            tools=self._sre_tools(),
            output_type=SRESubmission,
            agent_name="recovery-mitigation",
            model_settings=self._sre_model_settings(),
            usage_collector=usage_collector,
            deps=deps,
            run_ctx={"stage": "mitigation", "iteration": 0, "role": "recovery"},
        )

        if not result.completed or result.output is None:
            if not state.submitted:
                logger.warning("Recovery mitigation agent did not submit an answer.")
                return None
            submission = SRESubmission(
                answer=state.answer or "",
                justification=state.answer_justification or "",
                reflection=state.answer_reflection or "",
            )
        else:
            state.submitted = True
            state.answer = result.output.answer
            state.answer_justification = result.output.justification
            state.answer_reflection = result.output.reflection
            submission = SRESubmission(
                answer=result.output.answer,
                justification=result.output.justification,
                reflection=result.output.reflection,
            )

        # Append recovery result to shared file
        entry = (
            f"\n### Recovery Mitigation\n"
            f"**Mitigation**: {submission.answer}\n"
            f"**Justification**: {submission.justification}\n"
        )
        if submission.reflection:
            entry += f"**Agent Reflection**: {submission.reflection}\n"
        try:
            shared_file.append(entry)
        except Exception as e:
            logger.warning(f"Error writing recovery mitigation to shared file: {e}")

        # Append to stage outputs file
        if stage_outputs_file:
            with open(stage_outputs_file, "a") as f:
                f.write(f"**Mitigation**: {submission.answer}\n")
                f.write(f"**Justification**: {submission.justification}\n")
                if submission.reflection:
                    f.write(f"**Agent Reflection**: {submission.reflection}\n")

        logger.info(f"Recovery mitigation complete: {submission.answer}")
        return submission

    @staticmethod
    def _extract_benchmark_reasoning(benchmark_block: str, stage: str = "diagnosis") -> str:
        """Extract the 'reasoning' field from a benchmark_result block."""
        import json
        import re

        match = re.search(r"<oracle>\s*(.*?)\s*</oracle>", benchmark_block, re.DOTALL)
        if not match:
            return ""
        try:
            data = json.loads(match.group(1))
            key = "Mitigation" if stage == "mitigation" else "Diagnosis"
            return data.get(key, {}).get("reasoning", "")
        except (json.JSONDecodeError, AttributeError):
            return ""
