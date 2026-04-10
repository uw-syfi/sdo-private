"""LLM-driven synthesis and refinement of Crucible mitigation playbooks.

This module wraps the mitigation playbook data layer (``mitigation_playbook.py``)
with an LLM call loop. It renders the Jinja2 templates under
``sregym_agents/crucible/configs/prompts/v3/kb/`` to produce mitigation
playbook markdown for one of three scenarios:

1. Synthesize a new mitigation playbook from a successful mitigation.
2. Synthesize a new mitigation playbook from a recovery trajectory.
3. Refine an existing mitigation playbook using a fresh recovery trajectory.

Every LLM call is validated via :func:`validate_mitigation_playbook`. If
validation fails, the violations are fed back into the template via the
``validation_feedback`` variable and the call is retried up to
``MAX_VALIDATION_RETRIES`` additional times.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry_tracked

from .mitigation_playbook import (
    MitigationPlaybook,
    MitigationPlaybookValidationError,
    validate_mitigation_playbook,
)

if TYPE_CHECKING:
    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.recovery_reflection import RecoveryReflection

logger = logging.getLogger(__name__)


class MitigationPlaybookSynthesizer:
    """LLM-driven synthesis/refinement of mitigation playbooks."""

    MAX_VALIDATION_RETRIES = 2

    def __init__(self, model_id: str, renderer: PromptRenderer) -> None:
        self.model_id = model_id
        self.prompts = renderer
        # Set per-update by the owning ``StructuredKnowledgeBase``; LLM calls
        # report through ``arun_with_retry_tracked`` to this collector.
        self.usage_collector: UsageCollector | None = None

    async def synthesize_from_success(
        self,
        *,
        class_name: str,
        slug: str,
        stage_outputs: str,
        oracle_answer: str,
    ) -> MitigationPlaybook | None:
        """Synthesize a new mitigation playbook from a successful trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/synthesize_mitigation_playbook_from_success",
            template_vars={
                "class_name": class_name,
                "slug": slug,
                "current_iso": current_iso,
                "stage_outputs": stage_outputs,
                "oracle_answer": oracle_answer,
            },
            expected_slug=slug,
            log_label=f"synthesize_from_success[{slug}]",
        )

    async def synthesize_from_recovery(
        self,
        *,
        class_name: str,
        slug: str,
        stage_outputs: str,
        recovery_reflection: RecoveryReflection,
        oracle_answer: str,
    ) -> MitigationPlaybook | None:
        """Synthesize a new mitigation playbook from a recovery trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/synthesize_mitigation_playbook_from_recovery",
            template_vars={
                "class_name": class_name,
                "slug": slug,
                "current_iso": current_iso,
                "stage_outputs": stage_outputs,
                "oracle_answer": oracle_answer,
                "recovery_summary": recovery_reflection.summary,
                "recovery_observations": recovery_reflection.investigation_observations,
                "recovery_stage_failures": recovery_reflection.stage_failures,
            },
            expected_slug=slug,
            log_label=f"synthesize_from_recovery[{slug}]",
        )

    async def refine(
        self,
        *,
        existing: MitigationPlaybook,
        stage_outputs: str,
        recovery_reflection: RecoveryReflection,
        oracle_answer: str,
    ) -> MitigationPlaybook | None:
        """Refine an existing mitigation playbook using a fresh recovery trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/refine_mitigation_playbook",
            template_vars={
                "class_name": existing.class_name,
                "slug": existing.slug,
                "current_iso": current_iso,
                "existing_playbook": existing.to_markdown(),
                "oracle_answer": oracle_answer,
                "failed_stage_outputs": stage_outputs,
                "recovery_summary": recovery_reflection.summary,
                "recovery_observations": recovery_reflection.investigation_observations,
                "recovery_stage_failures": recovery_reflection.stage_failures,
            },
            expected_slug=existing.slug,
            log_label=f"refine[{existing.slug}]",
        )

    async def _call_with_validation(
        self,
        *,
        template_name: str,
        template_vars: dict[str, Any],
        expected_slug: str,
        log_label: str,
    ) -> MitigationPlaybook | None:
        """Render, call the LLM, and validate; retry on validation failure."""
        feedback = ""
        last_violations: list[str] = []
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)

        total_attempts = self.MAX_VALIDATION_RETRIES + 1
        for attempt in range(total_attempts):
            vars_with_feedback = {**template_vars, "validation_feedback": feedback}
            prompt = self.prompts.render(template_name, **vars_with_feedback)
            try:
                result = await arun_with_retry_tracked(
                    agent,
                    prompt,
                    agent_name=f"mitigation-playbook-{template_name.split('/')[-1]}",
                    usage_collector=self.usage_collector,
                )
            except Exception as exc:
                logger.error(f"Mitigation playbook {log_label}: LLM call failed on attempt {attempt + 1}: {exc}")
                return None

            text = result.output.strip()
            violations = validate_mitigation_playbook(text)
            if not violations:
                try:
                    playbook = MitigationPlaybook.parse(text)
                    playbook = playbook.model_copy(update={"slug": expected_slug})
                    return playbook
                except MitigationPlaybookValidationError as exc:
                    last_violations = exc.violations
                    feedback = "\n".join(f"- {v}" for v in last_violations)
                    logger.warning(
                        f"Mitigation playbook {log_label}: parse failed after clean validate "
                        f"(attempt {attempt + 1}): {exc}"
                    )
                    continue

            last_violations = violations
            feedback = "\n".join(f"- {v}" for v in violations)
            logger.warning(
                f"Mitigation playbook {log_label}: validation failed on attempt "
                f"{attempt + 1}/{total_attempts}: {violations}"
            )

        logger.error(
            f"Mitigation playbook {log_label}: giving up after {total_attempts} attempts. "
            f"Last violations: {last_violations}"
        )
        return None
