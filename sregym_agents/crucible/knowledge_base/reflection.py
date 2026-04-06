"""Reflection: classify agent failures and update prior files.

The reflection process has two phases:
1. **Reflect** — analyze the agent's trajectory to classify where in the
   pipeline the failure (or success) occurred.
2. **Apply** — make targeted edits to prior files based on the classification.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from .schema import SCHEMA_V2

if TYPE_CHECKING:
    from pathlib import Path

    from sregym_agents.crucible._prompts import PromptRenderer

    from .base import SessionFiles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PriorFileConfig:
    """Configuration for a single prior file that reflection can update."""

    template: str  # prompt template name, e.g. "kb/refine_triage_priors"
    filename: str  # output filename, e.g. "triage_priors.md"


PRIOR_FILES: dict[str, PriorFileConfig] = {
    "triage": PriorFileConfig("kb/refine_triage_priors", SCHEMA_V2.triage_priors),
}


# ---------------------------------------------------------------------------
# Reflector
# ---------------------------------------------------------------------------


class Reflector:
    """Classifies agent failure modes and updates prior files."""

    def __init__(self, kb_dir: Path, model_id: str, renderer: PromptRenderer):
        self.kb_dir = kb_dir
        self.model_id = model_id
        self.prompts = renderer

    async def _call_llm(self, prompt: str) -> str:
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await arun_with_retry(agent, prompt)
        return result.output

    # -- Phase 1: Reflect ---------------------------------------------------

    async def reflect(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
    ) -> str | None:
        """Classify where in the agent pipeline the failure occurred.

        Returns the raw classification text, or None if there is no session
        content to analyze.
        """
        stage_outputs = ""
        if stage_outputs_file and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text().strip()

        shared_session_parts = session_files.read_all()
        shared_session = "\n\n".join(shared_session_parts).strip()
        if not shared_session:
            logger.info("No shared session content; skipping reflection.")
            return None

        logger.info("Classifying agent failure modes...")
        prompt = self.prompts.render(
            "kb/classify_failure",
            stage_outputs=stage_outputs,
            shared_session=shared_session,
        )
        classification = await self._call_llm(prompt)
        logger.info("Failure classification:\n%s", classification)
        return classification

    # -- Phase 2: Apply -----------------------------------------------------

    async def _apply_to_stage(
        self,
        cfg: PriorFileConfig,
        classification: str,
        stage_outputs: str,
    ) -> None:
        """Update a single prior file based on the classification."""
        path = self.kb_dir / cfg.filename
        prior = path.read_text() if path.exists() else ""
        prompt = self.prompts.render(
            cfg.template,
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        result = await self._call_llm(prompt)
        path.write_text(result)
        logger.info("Updated %s", path)

    async def apply(self, classification: str, stage_outputs: str) -> None:
        """Update prior files based on the reflection."""
        for name, cfg in PRIOR_FILES.items():
            try:
                await self._apply_to_stage(cfg, classification, stage_outputs)
            except Exception as e:
                logger.error("Reflection apply error for %s: %s", name, e)

    # -- Combined entry point ------------------------------------------------

    async def run(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
    ) -> None:
        """Reflect on the trajectory and apply updates."""
        try:
            classification = await self.reflect(session_files, stage_outputs_file)
        except Exception as e:
            logger.error("Failed to classify failure: %s", e)
            return

        if classification is None:
            return

        stage_outputs = ""
        if stage_outputs_file and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text().strip()

        await self.apply(classification, stage_outputs)
