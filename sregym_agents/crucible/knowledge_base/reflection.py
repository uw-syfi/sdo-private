"""Reflection: classify agent failures and update prior files.

The reflection process has two phases:
1. **Reflect** — analyze the agent's trajectory to produce a structured
   ``FailureClassification`` identifying which pipeline stages failed.
2. **Apply** — make targeted edits to prior files *only* for the stages that
   failed, using the per-config ``apply()`` method.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from ..tools._kb_tools import TriagePriors
from .schema import SCHEMA_V2

if TYPE_CHECKING:
    from pathlib import Path

    from sregym_agents.crucible._prompts import PromptRenderer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured failure classification
# ---------------------------------------------------------------------------


class StageFailure(BaseModel):
    stage: Literal[
        "triage",
        "retrieval",
        "verification",
    ]
    description: str
    """What specifically went wrong at this stage."""
    evidence: str
    """Concrete quotes/citations from the trajectory that show the failure."""
    lesson: str
    """What guidance would prevent this failure in future runs."""


class FailureClassification(BaseModel):
    outcome: Literal["success", "failure"]
    stage_failures: list[StageFailure]
    """Empty on success. Multiple entries when failure spans stages."""
    summary: str
    """Brief overall narrative (2-3 sentences)."""


# ---------------------------------------------------------------------------
# Prior-update result
# ---------------------------------------------------------------------------


class PriorUpdateResult(BaseModel):
    """Structured output from the prior-refinement LLM call."""

    should_update: bool
    """False when the existing priors already cover this run's lessons."""

    diff_summary: str
    """Numbered list of edit decisions (or why no change was needed)."""

    updated_content: str
    """The full updated prior markdown. Ignored when should_update is False."""


class TriagePriorUpdateResult(BaseModel):
    """Structured output for triage prior refinement (YAML-backed)."""

    should_update: bool
    """False when the existing priors already cover this run's lessons."""

    diff_summary: str
    """Numbered list of edit decisions (or why no change was needed)."""

    updated_priors: TriagePriors
    """Structured triage priors — areas with names and hints."""


# ---------------------------------------------------------------------------
# Stage configuration (ABC + concrete configs)
# ---------------------------------------------------------------------------


class PriorFileConfig(ABC):
    """Base class for prior file configurations."""

    template: str
    filename: str

    @abstractmethod
    async def apply(
        self,
        reflector: Reflector,
        classification: str,
        stage_outputs: str,
    ) -> None:
        """Run the refinement LLM call and write the result."""


class MarkdownPriorConfig(PriorFileConfig):
    """For priors stored as free-form markdown (verification, diagnosis, etc.)."""

    def __init__(self, template: str, filename: str):
        self.template = template
        self.filename = filename

    async def apply(
        self,
        reflector: Reflector,
        classification: str,
        stage_outputs: str,
    ) -> None:
        path = reflector.kb_dir / self.filename
        prior = path.read_text() if path.exists() else ""
        prompt = reflector.prompts.render(
            self.template,
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        agent: Agent[None, PriorUpdateResult] = Agent(
            reflector.model_id,
            output_type=PriorUpdateResult,
        )
        result = await arun_with_retry(agent, prompt)
        if result.output.should_update:
            logger.info(
                "Updating %s:\n%s",
                self.filename,
                result.output.diff_summary,
            )
            path.write_text(result.output.updated_content)
        else:
            logger.info("No update needed for %s", self.filename)


class TriagePriorConfig(PriorFileConfig):
    """For triage priors stored as structured YAML."""

    def __init__(self) -> None:
        self.template = "kb/refine_triage_priors"
        self.filename = "triage_priors.yaml"

    async def apply(
        self,
        reflector: Reflector,
        classification: str,
        stage_outputs: str,
    ) -> None:
        import yaml

        from ..tools._kb_tools import load_triage_priors

        path = reflector.kb_dir / self.filename
        prior = load_triage_priors(path) if path.exists() else TriagePriors(areas=[])

        prior_text = (
            yaml.dump(prior.model_dump(), default_flow_style=False)
            if prior.areas
            else "(Empty — no triage areas defined yet)"
        )

        prompt = reflector.prompts.render(
            self.template,
            prior_priors=prior_text,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        agent: Agent[None, TriagePriorUpdateResult] = Agent(
            reflector.model_id,
            output_type=TriagePriorUpdateResult,
        )
        result = await arun_with_retry(agent, prompt)
        if result.output.should_update:
            logger.info(
                "Updating %s:\n%s",
                self.filename,
                result.output.diff_summary,
            )
            path.write_text(
                yaml.dump(
                    result.output.updated_priors.model_dump(),
                    default_flow_style=False,
                )
            )
        else:
            logger.info("No update needed for %s", self.filename)


PRIOR_FILES: dict[str, PriorFileConfig] = {
    "triage": TriagePriorConfig(),
    "verification": MarkdownPriorConfig(
        "kb/refine_verification_priors",
        SCHEMA_V2.verification_priors,
    ),
}

STAGE_TO_PRIOR: dict[str, str] = {
    "triage": "triage",
    "verification": "verification",
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

    # -- Phase 1: Reflect ---------------------------------------------------

    async def reflect(
        self,
        stage_outputs_file: Path,
    ) -> FailureClassification:
        """Classify where in the agent pipeline the failure occurred.

        Returns a structured ``FailureClassification``.
        """
        stage_outputs = stage_outputs_file.read_text().strip()

        logger.info("Classifying agent failure modes...")
        prompt = self.prompts.render(
            "kb/classify_failure",
            stage_outputs=stage_outputs,
        )
        agent: Agent[None, FailureClassification] = Agent(
            self.model_id,
            output_type=FailureClassification,
        )
        result = await arun_with_retry(agent, prompt)
        classification = result.output
        logger.info(
            "Failure classification: outcome=%s, stages=%s\n%s",
            classification.outcome,
            [sf.stage for sf in classification.stage_failures],
            classification.summary,
        )
        return classification

    # -- Phase 2: Apply -----------------------------------------------------

    async def apply(
        self,
        classification: FailureClassification,
        stage_outputs: str,
    ) -> None:
        """Update prior files based on the reflection."""
        if classification.outcome == "success" and not classification.stage_failures:
            logger.info("Unambiguous success; skipping prior updates.")
            return

        failed_stages = {sf.stage for sf in classification.stage_failures}
        for stage_label, prior_key in STAGE_TO_PRIOR.items():
            if stage_label not in failed_stages:
                continue
            cfg = PRIOR_FILES.get(prior_key)
            if cfg is None:
                continue
            relevant = [sf for sf in classification.stage_failures if sf.stage == stage_label]
            failure_text = "\n\n".join(
                f"### {sf.stage}\n{sf.description}\n\nEvidence: {sf.evidence}\n\nLesson: {sf.lesson}" for sf in relevant
            )
            try:
                await cfg.apply(self, failure_text, stage_outputs)
            except Exception as e:
                logger.error(
                    "Reflection apply error for %s: %s",
                    prior_key,
                    e,
                )

    # -- Combined entry point ------------------------------------------------

    async def run(
        self,
        stage_outputs_file: Path | None = None,
    ) -> None:
        """Reflect on the trajectory and apply updates."""
        if not stage_outputs_file or not stage_outputs_file.exists():
            logger.info("No stage output content; skipping reflection.")
            return

        stage_outputs_text = stage_outputs_file.read_text().strip()
        if not stage_outputs_text:
            logger.info("No stage output content; skipping reflection.")
            return

        try:
            classification = await self.reflect(stage_outputs_file)
        except Exception as e:
            logger.error("Failed to classify failure: %s", e)
            return

        await self.apply(classification, stage_outputs_text)
