"""Structured knowledge base with root-cause-first indexing and reflection."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from .base import (
    _MAX_CITATION_RETRIES,
    MAX_INJECTED_INCIDENTS,
    InjectedKB,
    KnowledgeBase,
    SessionFiles,
    _find_invalid_citations,
    _sanitize_app_name,
    _strip_benchmark_result,
    _strip_citation_wrappers,
)
from .reflection import Reflector
from .schema import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_V2,
    get_schema,
    migrate_to_current,
    write_schema_version,
)

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.config import CrucibleConfig

logger = logging.getLogger(__name__)


class StructuredKnowledgeBase(KnowledgeBase):
    """Maintains a structured knowledge base with root-cause-first indexing."""

    def __init__(
        self,
        kb_dir: Path,
        model_id: str,
        app_name: str = "unknown",
        seed_kb_dir: Path | None = None,
        *,
        config: CrucibleConfig | None = None,
        renderer: PromptRenderer,
    ):
        from sregym_agents.crucible.config import CrucibleConfig as _CrucibleConfig

        if config is None:
            config = _CrucibleConfig()
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name
        self.app_dir = self.kb_dir / _sanitize_app_name(self.app_name)
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.include_benchmark_results = config.include_benchmark_results
        self.enable_reflection = config.enable_reflection
        self.include_incident_files = config.include_incident_files
        self.prompts = renderer
        self.schema = SCHEMA_V2
        self._reflector = Reflector(self.kb_dir, model_id, renderer)

        if seed_kb_dir is not None:
            self._seed_from(Path(seed_kb_dir))

        # Migrate existing KB to current schema (renames files in place).
        migrate_to_current(self.kb_dir)
        write_schema_version(self.kb_dir, CURRENT_SCHEMA_VERSION)

    def _seed_from(self, seed_kb_dir: Path) -> None:
        """Copy KB files from a seed directory if local files don't exist yet.

        Reads the seed KB using its own schema version (never modifies the seed).
        """
        seed_schema = get_schema(seed_kb_dir)
        sanitized = _sanitize_app_name(self.app_name)
        seed_app_dir = seed_kb_dir / sanitized

        # Per-app summary
        seed_summary = seed_app_dir / seed_schema.summary
        if not self.summary_path.exists() and seed_summary.exists():
            shutil.copy2(seed_summary, self.summary_path)
            logger.info(f"Seeded summary from {seed_summary}")

        # Per-app incidents
        seed_incidents = seed_app_dir / seed_schema.incidents_dir
        if seed_incidents.is_dir() and not self.incidents_dir.exists():
            self.incidents_dir.mkdir(parents=True, exist_ok=True)
            for f in seed_incidents.glob("*.md"):
                shutil.copy2(f, self.incidents_dir / f.name)
            logger.info(f"Seeded incidents from {seed_incidents}")

        # Per-app architecture
        seed_architecture = seed_app_dir / seed_schema.architecture
        if not self.architecture_path.exists() and seed_architecture.exists():
            shutil.copy2(seed_architecture, self.architecture_path)
            logger.info(f"Seeded architecture from {seed_architecture}")
        elif seed_app_dir.is_dir() and not seed_architecture.exists():
            logger.warning(f"Seed app dir {seed_app_dir} exists but {seed_schema.architecture} is absent")

        # Root-level operational lessons
        seed_lessons = seed_kb_dir / seed_schema.lessons
        if not self.lessons_path.exists() and seed_lessons.exists():
            shutil.copy2(seed_lessons, self.lessons_path)
            logger.info(f"Seeded lessons from {seed_lessons}")

        # Root-level priors (seed may use old filenames; we write to current schema)
        for prior_field in ("diagnosis_priors", "triage_priors", "arbitration_priors"):
            seed_filename = getattr(seed_schema, prior_field)
            dest_path = self.kb_dir / getattr(self.schema, prior_field)
            seed_file = seed_kb_dir / seed_filename
            if not dest_path.exists() and seed_file.exists():
                shutil.copy2(seed_file, dest_path)
                logger.info(f"Seeded {seed_filename} -> {dest_path.name}")

    @property
    def summary_path(self) -> Path:
        return self.app_dir / self.schema.summary

    @property
    def lessons_path(self) -> Path:
        return self.kb_dir / self.schema.lessons

    @property
    def architecture_path(self) -> Path:
        return self.app_dir / self.schema.architecture

    @property
    def incidents_dir(self) -> Path:
        return self.app_dir / self.schema.incidents_dir

    @property
    def diagnosis_priors_path(self) -> Path:
        return self.kb_dir / self.schema.diagnosis_priors

    @property
    def triage_priors_path(self) -> Path:
        return self.kb_dir / self.schema.triage_priors

    @property
    def arbitration_priors_path(self) -> Path:
        return self.kb_dir / self.schema.arbitration_priors

    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy KB files into target_dir for agent consumption.

        Returns an InjectedKB with paths for each copied file, or None per field
        if the source doesn't exist.
        """
        result = InjectedKB()

        try:
            dest = target_dir / self.schema.summary
            shutil.copy2(self.summary_path, dest)
            result.summary = dest
            logger.info(f"Knowledge base: copied prior summary to {dest}")
        except FileNotFoundError:
            logger.info("Knowledge base: no prior summary found; starting fresh.")

        if self.lessons_path.exists():
            dest_lessons = target_dir / self.schema.lessons
            shutil.copy2(self.lessons_path, dest_lessons)
            result.lessons = dest_lessons
            logger.info(f"Knowledge base: copied lessons to {dest_lessons}")
        else:
            logger.info("Knowledge base: no prior lessons file found.")

        if self.architecture_path.exists():
            dest_arch = target_dir / self.schema.architecture
            shutil.copy2(self.architecture_path, dest_arch)
            result.architecture = dest_arch
            logger.info(f"Knowledge base: copied architecture to {dest_arch}")
        else:
            logger.warning("Knowledge base: no architecture file found.")

        if self.include_incident_files and self.incidents_dir.is_dir():
            incident_files = sorted(self.incidents_dir.glob("*.md"))[-MAX_INJECTED_INCIDENTS:]
            if incident_files:
                dest_incidents = target_dir / self.schema.incidents_dir
                dest_incidents.mkdir(exist_ok=True)
                for f in incident_files:
                    shutil.copy2(f, dest_incidents / f.name)
                result.incidents_dir = dest_incidents
                logger.info(f"Knowledge base: copied {len(incident_files)} incident(s) to {dest_incidents}")
        elif not self.include_incident_files:
            logger.info("Knowledge base: incident file injection disabled by include_incident_files=false")

        # Prior files (root-level, cross-app)
        for prior_field in ("diagnosis_priors", "triage_priors", "arbitration_priors"):
            filename = getattr(self.schema, prior_field)
            src = self.kb_dir / filename
            if src.exists():
                dest = target_dir / filename
                shutil.copy2(src, dest)
                setattr(result, prior_field, dest)
                logger.info(f"Knowledge base: copied {filename} to {dest}")

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
        summary_files = sorted(self.kb_dir.glob(f"*/{self.schema.summary}"))
        if not summary_files:
            logger.info(f"No {self.schema.summary} files found; skipping lessons extraction.")
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
        if self.enable_reflection:
            await self._reflector.run(session_files, stage_outputs_file)
        else:
            logger.info("Reflection disabled; skipping.")
