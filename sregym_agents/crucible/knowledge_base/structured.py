"""Structured knowledge base with root-cause-first indexing and heuristic refinement."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from .base import (
    _MAX_CITATION_RETRIES,
    KB_ARBITRATION_HEURISTICS_FILENAME,
    KB_ARCHITECTURE_FILENAME,
    KB_DIAGNOSIS_HEURISTICS_FILENAME,
    KB_INCIDENTS_DIRNAME,
    KB_LESSONS_FILENAME,
    KB_SUMMARY_FILENAME,
    KB_TRIAGE_HEURISTICS_FILENAME,
    MAX_INJECTED_INCIDENTS,
    InjectedKB,
    KnowledgeBase,
    SessionFiles,
    _find_invalid_citations,
    _sanitize_app_name,
    _strip_benchmark_result,
    _strip_citation_wrappers,
)

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.orchestrator import CrucibleFlags

logger = logging.getLogger(__name__)


class HeuristicRefiner:
    """Classifies agent failure modes and refines heuristic guidance files."""

    def __init__(self, kb_dir: Path, model_id: str, renderer: PromptRenderer):
        self.kb_dir = kb_dir
        self.model_id = model_id
        self.prompts = renderer

    @property
    def diagnosis_heuristics_path(self) -> Path:
        return self.kb_dir / KB_DIAGNOSIS_HEURISTICS_FILENAME

    @property
    def triage_heuristics_path(self) -> Path:
        return self.kb_dir / KB_TRIAGE_HEURISTICS_FILENAME

    @property
    def arbitration_heuristics_path(self) -> Path:
        return self.kb_dir / KB_ARBITRATION_HEURISTICS_FILENAME

    async def _call_llm(self, prompt: str) -> str:
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await arun_with_retry(agent, prompt)
        return result.output

    async def _classify_failure(self, stage_outputs: str, shared_session: str) -> str:
        """Classify where in the agent pipeline the failure (or success) occurred."""
        prompt = self.prompts.render(
            "kb/classify_failure",
            stage_outputs=stage_outputs,
            shared_session=shared_session,
        )
        return await self._call_llm(prompt)

    async def _refine_diagnosis_heuristics(self, classification: str, stage_outputs: str) -> None:
        prior = self.diagnosis_heuristics_path.read_text() if self.diagnosis_heuristics_path.exists() else ""
        prompt = self.prompts.render(
            "kb/refine_diagnosis_heuristics",
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        result = await self._call_llm(prompt)
        self.diagnosis_heuristics_path.write_text(result)
        logger.info(f"Diagnosis heuristics updated at {self.diagnosis_heuristics_path}")

    async def _refine_triage_heuristics(self, classification: str, stage_outputs: str) -> None:
        prior = self.triage_heuristics_path.read_text() if self.triage_heuristics_path.exists() else ""
        prompt = self.prompts.render(
            "kb/refine_triage_heuristics",
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        result = await self._call_llm(prompt)
        self.triage_heuristics_path.write_text(result)
        logger.info(f"Triage heuristics updated at {self.triage_heuristics_path}")

    async def _refine_arbitration_heuristics(self, classification: str, stage_outputs: str) -> None:
        prior = self.arbitration_heuristics_path.read_text() if self.arbitration_heuristics_path.exists() else ""
        prompt = self.prompts.render(
            "kb/refine_arbitration_heuristics",
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        result = await self._call_llm(prompt)
        self.arbitration_heuristics_path.write_text(result)
        logger.info(f"Arbitration heuristics updated at {self.arbitration_heuristics_path}")

    async def refine(self, session_files: SessionFiles, stage_outputs_file: Path | None = None) -> None:
        """Two-stage heuristic refinement: classify failure, then targeted edits."""
        stage_outputs = ""
        if stage_outputs_file and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text().strip()

        shared_session_parts = session_files.read_all()
        shared_session = "\n\n".join(shared_session_parts).strip()
        if not shared_session:
            logger.info("No shared session content; skipping heuristic refinement.")
            return

        # Stage 1: classify failure
        logger.info("Classifying agent failure modes...")
        try:
            classification = await self._classify_failure(stage_outputs, shared_session)
        except Exception as e:
            logger.error(f"Failed to classify failure: {e}")
            return
        logger.info(f"Failure classification:\n{classification}")

        # Stage 2: targeted refinement based on classification
        classification_lower = classification.lower()

        needs_diagnosis = any(kw in classification_lower for kw in ("reasoning", "retrieval", "other", "success"))
        needs_triage = "triage" in classification_lower
        needs_arbitration = "arbitration" in classification_lower

        refinement_tasks = []
        if needs_diagnosis:
            refinement_tasks.append(self._refine_diagnosis_heuristics(classification, stage_outputs))
        if needs_triage:
            refinement_tasks.append(self._refine_triage_heuristics(classification, stage_outputs))
        if needs_arbitration:
            refinement_tasks.append(self._refine_arbitration_heuristics(classification, stage_outputs))

        if not refinement_tasks:
            logger.info("No heuristic refinement needed based on classification.")
            return

        results = await asyncio.gather(*refinement_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Heuristic refinement error: {r}")


class StructuredKnowledgeBase(KnowledgeBase):
    """Maintains a structured knowledge base with root-cause-first indexing."""

    def __init__(
        self,
        kb_dir: Path,
        model_id: str,
        app_name: str = "unknown",
        seed_kb_dir: Path | None = None,
        *,
        flags: CrucibleFlags | None = None,
        renderer: PromptRenderer,
    ):
        from sregym_agents.crucible.orchestrator import CrucibleFlags as _CrucibleFlags

        if flags is None:
            flags = _CrucibleFlags()
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name
        self.app_dir = self.kb_dir / _sanitize_app_name(self.app_name)
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.include_benchmark_results = flags.include_benchmark_results
        self.enable_heuristic_refinement = flags.enable_heuristic_refinement
        self.include_incident_files = flags.include_incident_files
        self.prompts = renderer
        self._refiner = HeuristicRefiner(self.kb_dir, model_id, renderer)

        if seed_kb_dir is not None:
            self._seed_from(Path(seed_kb_dir))

    def _seed_from(self, seed_kb_dir: Path) -> None:
        """Copy KB files from a seed directory if local files don't exist yet."""
        sanitized = _sanitize_app_name(self.app_name)
        seed_app_dir = seed_kb_dir / sanitized

        # Per-app summary
        seed_summary = seed_app_dir / KB_SUMMARY_FILENAME
        if not self.summary_path.exists() and seed_summary.exists():
            shutil.copy2(seed_summary, self.summary_path)
            logger.info(f"Seeded summary from {seed_summary}")

        # Per-app incidents
        seed_incidents = seed_app_dir / KB_INCIDENTS_DIRNAME
        if seed_incidents.is_dir() and not self.incidents_dir.exists():
            self.incidents_dir.mkdir(parents=True, exist_ok=True)
            for f in seed_incidents.glob("*.md"):
                shutil.copy2(f, self.incidents_dir / f.name)
            logger.info(f"Seeded incidents from {seed_incidents}")

        # Per-app architecture
        seed_architecture = seed_app_dir / KB_ARCHITECTURE_FILENAME
        if not self.architecture_path.exists() and seed_architecture.exists():
            shutil.copy2(seed_architecture, self.architecture_path)
            logger.info(f"Seeded architecture from {seed_architecture}")
        elif seed_app_dir.is_dir() and not seed_architecture.exists():
            logger.warning(f"Seed app dir {seed_app_dir} exists but {KB_ARCHITECTURE_FILENAME} is absent")

        # Root-level operational lessons
        seed_lessons = seed_kb_dir / KB_LESSONS_FILENAME
        if not self.lessons_path.exists() and seed_lessons.exists():
            shutil.copy2(seed_lessons, self.lessons_path)
            logger.info(f"Seeded lessons from {seed_lessons}")

        # Root-level trained heuristics
        for filename, dest_path in [
            (KB_DIAGNOSIS_HEURISTICS_FILENAME, self.diagnosis_heuristics_path),
            (KB_TRIAGE_HEURISTICS_FILENAME, self.triage_heuristics_path),
            (KB_ARBITRATION_HEURISTICS_FILENAME, self.arbitration_heuristics_path),
        ]:
            seed_file = seed_kb_dir / filename
            if not dest_path.exists() and seed_file.exists():
                shutil.copy2(seed_file, dest_path)
                logger.info(f"Seeded {filename} from {seed_file}")

    @property
    def summary_path(self) -> Path:
        return self.app_dir / KB_SUMMARY_FILENAME

    @property
    def lessons_path(self) -> Path:
        return self.kb_dir / KB_LESSONS_FILENAME

    @property
    def architecture_path(self) -> Path:
        return self.app_dir / KB_ARCHITECTURE_FILENAME

    @property
    def incidents_dir(self) -> Path:
        return self.app_dir / KB_INCIDENTS_DIRNAME

    @property
    def diagnosis_heuristics_path(self) -> Path:
        return self._refiner.diagnosis_heuristics_path

    @property
    def triage_heuristics_path(self) -> Path:
        return self._refiner.triage_heuristics_path

    @property
    def arbitration_heuristics_path(self) -> Path:
        return self._refiner.arbitration_heuristics_path

    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy KB files into target_dir for agent consumption.

        Returns an InjectedKB with paths for each copied file, or None per field
        if the source doesn't exist.
        """
        result = InjectedKB()

        try:
            dest = target_dir / KB_SUMMARY_FILENAME
            shutil.copy2(self.summary_path, dest)
            result.summary = dest
            logger.info(f"Knowledge base: copied prior summary to {dest}")
        except FileNotFoundError:
            logger.info("Knowledge base: no prior summary found; starting fresh.")

        if self.lessons_path.exists():
            dest_lessons = target_dir / KB_LESSONS_FILENAME
            shutil.copy2(self.lessons_path, dest_lessons)
            result.lessons = dest_lessons
            logger.info(f"Knowledge base: copied lessons to {dest_lessons}")
        else:
            logger.info("Knowledge base: no prior lessons file found.")

        if self.architecture_path.exists():
            dest_arch = target_dir / KB_ARCHITECTURE_FILENAME
            shutil.copy2(self.architecture_path, dest_arch)
            result.architecture = dest_arch
            logger.info(f"Knowledge base: copied architecture to {dest_arch}")
        else:
            logger.warning("Knowledge base: no architecture file found.")

        if self.include_incident_files and self.incidents_dir.is_dir():
            incident_files = sorted(self.incidents_dir.glob("*.md"))[-MAX_INJECTED_INCIDENTS:]
            if incident_files:
                dest_incidents = target_dir / KB_INCIDENTS_DIRNAME
                dest_incidents.mkdir(exist_ok=True)
                for f in incident_files:
                    shutil.copy2(f, dest_incidents / f.name)
                result.incidents_dir = dest_incidents
                logger.info(f"Knowledge base: copied {len(incident_files)} incident(s) to {dest_incidents}")
        elif not self.include_incident_files:
            logger.info("Knowledge base: incident file injection disabled by include_incident_files=false")

        try:
            result.triage_additions = await self.extract_triage_additions()
        except Exception as e:
            logger.warning(f"Failed to extract triage additions: {e}")
            result.triage_additions = None

        # Trained heuristic files (root-level, cross-app)
        for filename, attr in [
            (KB_DIAGNOSIS_HEURISTICS_FILENAME, "diagnosis_heuristics"),
            (KB_TRIAGE_HEURISTICS_FILENAME, "triage_heuristics"),
            (KB_ARBITRATION_HEURISTICS_FILENAME, "arbitration_heuristics"),
        ]:
            src = self.kb_dir / filename
            if src.exists():
                dest = target_dir / filename
                shutil.copy2(src, dest)
                setattr(result, attr, dest)
                logger.info(f"Knowledge base: copied {filename} to {dest}")

        return result

    async def extract_triage_additions(self) -> str:
        """Extract a triage checklist supplement from KB lessons and per-app summary.

        Returns the triage checklist text, or empty string if source files are missing.
        """
        operational_lessons = ""
        if self.lessons_path.exists():
            operational_lessons = self.lessons_path.read_text().strip()

        long_term_summary = ""
        if self.summary_path.exists():
            long_term_summary = self.summary_path.read_text().strip()

        if not operational_lessons and not long_term_summary:
            logger.info("No KB content available for triage additions; returning empty.")
            return ""

        prompt = self.prompts.render(
            "kb/extract_triage_checklist",
            operational_lessons=operational_lessons,
            long_term_summary=long_term_summary,
        )
        result = await self._call_llm(prompt)
        logger.info(f"Triage additions extracted ({len(result)} chars)")
        return result

    async def _call_llm(self, prompt: str) -> str:
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await arun_with_retry(agent, prompt)
        return result.output

    def _save_incident(self, session_summary: str, session_content: str) -> str:
        self.incidents_dir.mkdir(parents=True, exist_ok=True)
        incident_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.incidents_dir / f"{incident_id}.md"
        path.write_text(f"{session_summary}\n\n---\n\n{session_content}")
        logger.info(f"Saved incident summary to {path}")
        return incident_id

    async def _summarize_session(self, content: str) -> str:
        prompt = self.prompts.render(
            "kb/summarize_session",
            content=content,
            include_benchmark_results=self.include_benchmark_results,
        )
        return await self._call_llm(prompt)

    async def _merge_into_long_term_summary(
        self, session_summary: str, prior_summary: str, incident_ref: str = ""
    ) -> str:
        prompt = self.prompts.render(
            "kb/merge_summary", session_summary=session_summary, prior_summary=prior_summary, incident_ref=incident_ref
        )
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await arun_with_retry(agent, prompt)
        output = result.output

        for attempt in range(_MAX_CITATION_RETRIES):
            invalid = _find_invalid_citations(output, self.incidents_dir)
            if not invalid:
                break
            valid_files = sorted(f.name for f in self.incidents_dir.glob("*.md")) if self.incidents_dir.is_dir() else []
            correction = (
                f"The following incident citations are invalid (files do not exist): {invalid}\n"
                f"Valid incident files are: {valid_files}\n"
                "Please output the COMPLETE updated Long-Term Summary again with corrected citations. "
                "Use the exact format {{ref:incidents/FILENAME.md}} for each citation."
            )
            logger.warning(f"Citation validation failed (attempt {attempt + 1}/{_MAX_CITATION_RETRIES}): {invalid}")
            result = await arun_with_retry(agent, correction, message_history=result.all_messages())
            output = result.output

        return _strip_citation_wrappers(output)

    async def _extract_operational_lessons(self, long_term_summary: str) -> str:
        prompt = self.prompts.render("kb/extract_lessons", long_term_summary=long_term_summary)
        return await self._call_llm(prompt)

    async def _distill_lessons(self) -> None:
        """Re-distill cross-cutting operational lessons from all per-app summaries."""
        summary_files = sorted(self.kb_dir.glob(f"*/{KB_SUMMARY_FILENAME}"))
        if not summary_files:
            logger.info(f"No {KB_SUMMARY_FILENAME} files found; skipping lessons extraction.")
            return

        parts = []
        for sf in summary_files:
            text = sf.read_text().strip()
            if text:
                parts.append(text)

        long_term_summary = "\n\n".join(parts)
        if not long_term_summary:
            logger.info("All long-term summaries are empty; skipping lessons extraction.")
            return

        logger.info("Extracting operational lessons from long-term summary...")
        try:
            lessons = await self._extract_operational_lessons(long_term_summary)
        except Exception as e:
            logger.error(f"Failed to extract operational lessons: {e}")
            return

        self.lessons_path.write_text(lessons)
        logger.info(f"Operational lessons written to {self.lessons_path}")

    async def update(self, session_files: SessionFiles, stage_outputs_file: Path | None = None) -> None:
        """Summarize the completed session and update the knowledge base."""
        parts = session_files.read_all()
        if not parts:
            logger.warning("No shared files found; skipping knowledge base update.")
            return

        raw = "\n\n".join(parts)
        if self.include_benchmark_results:
            content = raw.strip()
        else:
            content = _strip_benchmark_result(raw)
        if not content:
            logger.warning("Shared file is empty after stripping benchmark results; skipping.")
            return

        logger.info("Generating session summary from shared markdown file...")
        try:
            session_summary = await self._summarize_session(content)
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")
            return

        logger.info(f"Session summary:\n{session_summary}")

        incident_ref = ""
        if self.include_incident_files:
            incident_id = self._save_incident(session_summary, content)
            incident_ref = f"incidents/{incident_id}.md"
        else:
            logger.info("Skipping incident file save (include_incident_files=false)")

        prior_summary = self.summary_path.read_text() if self.summary_path.exists() else ""
        logger.info("Merging into long-term summary...")
        try:
            updated = await self._merge_into_long_term_summary(
                session_summary, prior_summary, incident_ref=incident_ref
            )
        except Exception as e:
            logger.error(f"Failed to merge into long-term summary: {e}")
            return

        self.summary_path.write_text(updated)
        logger.info(f"Long-term summary updated at {self.summary_path}")

        await self._distill_lessons()
        if self.enable_heuristic_refinement:
            await self._refiner.refine(session_files, stage_outputs_file)
        else:
            logger.info("Heuristic refinement disabled; skipping.")
