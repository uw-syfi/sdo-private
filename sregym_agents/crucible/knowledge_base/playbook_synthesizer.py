"""LLM-driven synthesis, refinement, and consolidation of Crucible playbooks.

This module wraps the playbook data layer (``playbook.py``) with an LLM
call loop. It renders the Jinja2 templates under
``sregym_agents/crucible/configs/prompts/v3/kb/`` to produce playbook
markdown for one of four scenarios:

1. Synthesize a new playbook from a successful diagnosis.
2. Synthesize a new playbook from a recovery trajectory.
3. Refine an existing playbook using a fresh recovery trajectory.
4. Consolidate multiple near-duplicate playbooks into a single winner.

Every LLM call is validated via :func:`validate_playbook`. If validation
fails, the violations are fed back into the template via the
``validation_feedback`` variable and the call is retried up to
``MAX_VALIDATION_RETRIES`` additional times.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .playbook import (
    Playbook,
    PlaybookValidationError,
    validate_playbook,
)

if TYPE_CHECKING:
    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver
    from sregym_agents.crucible.recovery_reflection import RecoveryReflection

logger = logging.getLogger(__name__)


class PlaybookSynthesizer:
    """LLM-driven synthesis/refinement/consolidation of playbooks."""

    MAX_VALIDATION_RETRIES = 2

    def __init__(self, renderer: PromptRenderer, driver: AgentDriver) -> None:
        self.prompts = renderer
        self._driver = driver
        self.usage_collector: UsageCollector | None = None

    async def synthesize_from_success(
        self,
        *,
        class_name: str,
        slug: str,
        stage_outputs: str,
        oracle_answer: str,
    ) -> Playbook | None:
        """Synthesize a new playbook from a successful diagnosis trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/synthesize_playbook_from_success",
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
    ) -> Playbook | None:
        """Synthesize a new playbook from a recovery trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/synthesize_playbook_from_recovery",
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
        existing: Playbook,
        stage_outputs: str,
        recovery_reflection: RecoveryReflection,
        oracle_answer: str,
    ) -> Playbook | None:
        """Refine an existing playbook using a fresh recovery trajectory."""
        current_iso = datetime.now(timezone.utc).isoformat()
        return await self._call_with_validation(
            template_name="kb/refine_playbook",
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

    async def consolidate(
        self,
        *,
        winner_class_name: str,
        winner_slug: str,
        winner_playbook: Playbook | None,
        loser_playbooks: list[Playbook],
        combined_seen: int,
        stage_outputs: str,
        recovery_summary: str,
    ) -> Playbook | None:
        """Consolidate multiple near-duplicate playbooks into a single winner."""
        current_iso = datetime.now(timezone.utc).isoformat()
        winner_md = winner_playbook.to_markdown() if winner_playbook else "(none)"
        loser_mds = [lp.to_markdown() for lp in loser_playbooks]
        return await self._call_with_validation(
            template_name="kb/merge_playbooks",
            template_vars={
                "winner_class_name": winner_class_name,
                "winner_slug": winner_slug,
                "current_iso": current_iso,
                "combined_seen": combined_seen,
                "winner_playbook": winner_md,
                "loser_playbooks": loser_mds,
                "stage_outputs": stage_outputs,
                "recovery_summary": recovery_summary,
            },
            expected_slug=winner_slug,
            log_label=f"consolidate[{winner_slug}]",
        )

    async def _call_with_validation(
        self,
        *,
        template_name: str,
        template_vars: dict[str, Any],
        expected_slug: str,
        log_label: str,
    ) -> Playbook | None:
        """Render, call the LLM, and validate; retry on validation failure."""
        feedback = ""
        last_violations: list[str] = []

        total_attempts = self.MAX_VALIDATION_RETRIES + 1
        for attempt in range(total_attempts):
            vars_with_feedback = {**template_vars, "validation_feedback": feedback}
            prompt = self.prompts.render(template_name, **vars_with_feedback)
            agent_name = f"playbook-{template_name.split('/')[-1]}"
            try:
                dr_result = await self._driver.run(
                    prompt=prompt,
                    output_type=str,
                    agent_name=agent_name,
                    usage_collector=self.usage_collector,
                )
                text = (dr_result.output or "").strip()
            except Exception as exc:
                logger.error(f"Playbook {log_label}: LLM call failed on attempt {attempt + 1}: {exc}")
                return None

            violations = validate_playbook(text)
            if not violations:
                try:
                    playbook = Playbook.parse(text)
                    playbook = playbook.model_copy(update={"slug": expected_slug})
                    return playbook
                except PlaybookValidationError as exc:
                    last_violations = exc.violations
                    feedback = "\n".join(f"- {v}" for v in last_violations)
                    logger.warning(
                        f"Playbook {log_label}: parse failed after clean validate (attempt {attempt + 1}): {exc}"
                    )
                    continue

            last_violations = violations
            feedback = "\n".join(f"- {v}" for v in violations)
            logger.warning(
                f"Playbook {log_label}: validation failed on attempt {attempt + 1}/{total_attempts}: {violations}"
            )

        logger.error(
            f"Playbook {log_label}: giving up after {total_attempts} attempts. Last violations: {last_violations}"
        )
        return None
