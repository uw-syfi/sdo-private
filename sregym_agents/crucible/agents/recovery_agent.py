"""RecoveryAgent — encapsulates grounded diagnosis and mitigation recovery."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver
    from sregym_agents.crucible.tools import SharedFile, SRESubmission

logger = logging.getLogger(__name__)


class RecoveryAgent:
    """Encapsulates grounded diagnosis and mitigation recovery."""

    def __init__(
        self,
        driver: AgentDriver,
        model_id: Any,
        renderer: PromptRenderer,
    ) -> None:
        self._driver = driver
        self._model_id = model_id
        self._renderer = renderer

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
