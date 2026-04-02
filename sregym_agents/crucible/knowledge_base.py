"""Crucible knowledge base: cross-problem learning via pydantic-ai Agent."""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from pydantic_ai import Agent

from sregym_agents.crucible._prompts import _render

logger = logging.getLogger(__name__)

KB_SUMMARY_FILENAME = "long_term_summary.md"
KB_LESSONS_FILENAME = "operational_lessons.md"
KB_ARCHITECTURE_FILENAME = "architecture.md"
KB_INCIDENTS_DIRNAME = "incidents"
KB_DIAGNOSIS_HEURISTICS_FILENAME = "diagnosis_heuristics.md"
KB_TRIAGE_HEURISTICS_FILENAME = "triage_heuristics.md"
KB_ARBITRATION_HEURISTICS_FILENAME = "arbitration_heuristics.md"
MAX_INJECTED_INCIDENTS = 100


@dataclasses.dataclass
class InjectedKB:
    """Paths to KB files injected into the experiment environment."""

    summary: Path | None = None
    lessons: Path | None = None
    architecture: Path | None = None
    incidents_dir: Path | None = None
    triage_additions: str | None = None
    diagnosis_heuristics: Path | None = None
    triage_heuristics: Path | None = None
    arbitration_heuristics: Path | None = None


_BENCHMARK_RESULT_RE = re.compile(r"<benchmark_result>.*?</benchmark_result>", re.DOTALL)
_CITATION_RE = re.compile(r"\{\{ref:(incidents/[^}]+)\}\}")
_MAX_CITATION_RETRIES = 2


def _strip_benchmark_result(text: str) -> str:
    """Remove all <benchmark_result>...</benchmark_result> blocks from text."""
    return _BENCHMARK_RESULT_RE.sub("", text).strip()


def _extract_citations(text: str) -> list[str]:
    """Extract all {{ref:incidents/...}} citation values from text."""
    return _CITATION_RE.findall(text)


def _find_invalid_citations(text: str, incidents_dir: Path) -> list[str]:
    """Return citation values that reference non-existent incident files."""
    citations = _extract_citations(text)
    invalid = []
    for ref in citations:
        # ref is like "incidents/20260324_010224.md"
        filename = Path(ref).name
        if not (incidents_dir / filename).exists():
            invalid.append(ref)
    return invalid


def _strip_citation_wrappers(text: str) -> str:
    """Replace {{ref:incidents/foo.md}} with incidents/foo.md."""
    return _CITATION_RE.sub(r"\1", text)


def _sanitize_app_name(name: str) -> str:
    """Sanitize an application name for use as a directory name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_").lower() or "unknown"


class KnowledgeBase(abc.ABC):
    """Abstract base class for knowledge base implementations."""

    @abc.abstractmethod
    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy KB files into target_dir for agent consumption."""

    @abc.abstractmethod
    async def update(self, stage_outputs_file: Path | None = None) -> None:
        """Update the knowledge base from the completed session."""

    @abc.abstractmethod
    async def extract_triage_additions(self) -> str:
        """Extract triage checklist additions from KB content."""


class StructuredKnowledgeBase(KnowledgeBase):
    """Maintains a structured knowledge base with root-cause-first indexing."""

    def __init__(
        self,
        shared_files: list[Path],
        kb_dir: Path,
        model_id: str,
        app_name: str = "unknown",
        seed_kb_dir: Path | None = None,
        include_benchmark_results: bool = False,
        enable_heuristic_refinement: bool = True,
        include_incident_files: bool = True,
    ):
        self.shared_files = shared_files
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name
        self.app_dir = self.kb_dir / _sanitize_app_name(self.app_name)
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.include_benchmark_results = include_benchmark_results
        self.enable_heuristic_refinement = enable_heuristic_refinement
        self.include_incident_files = include_incident_files

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
        return self.kb_dir / KB_DIAGNOSIS_HEURISTICS_FILENAME

    @property
    def triage_heuristics_path(self) -> Path:
        return self.kb_dir / KB_TRIAGE_HEURISTICS_FILENAME

    @property
    def arbitration_heuristics_path(self) -> Path:
        return self.kb_dir / KB_ARBITRATION_HEURISTICS_FILENAME

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

        prompt = _render(
            "kb/extract_triage_checklist",
            operational_lessons=operational_lessons,
            long_term_summary=long_term_summary,
        )
        result = await self._call_llm(prompt)
        logger.info(f"Triage additions extracted ({len(result)} chars)")
        return result

    async def _call_llm(self, prompt: str) -> str:
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await agent.run(prompt)
        return result.output

    def _save_incident(self, session_summary: str, session_content: str) -> str:
        self.incidents_dir.mkdir(parents=True, exist_ok=True)
        incident_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.incidents_dir / f"{incident_id}.md"
        path.write_text(f"{session_summary}\n\n---\n\n{session_content}")
        logger.info(f"Saved incident summary to {path}")
        return incident_id

    async def _summarize_session(self, content: str) -> str:
        prompt = _render(
            "kb/summarize_session",
            content=content,
            include_benchmark_results=self.include_benchmark_results,
        )
        return await self._call_llm(prompt)

    async def _merge_into_long_term_summary(
        self, session_summary: str, prior_summary: str, incident_ref: str = ""
    ) -> str:
        prompt = _render(
            "kb/merge_summary", session_summary=session_summary, prior_summary=prior_summary, incident_ref=incident_ref
        )
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await agent.run(prompt)
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
            result = await agent.run(correction, message_history=result.all_messages())
            output = result.output

        return _strip_citation_wrappers(output)

    async def _extract_operational_lessons(self, long_term_summary: str) -> str:
        prompt = _render("kb/extract_lessons", long_term_summary=long_term_summary)
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

    async def _classify_failure(self, stage_outputs: str, shared_session: str) -> str:
        """Classify where in the agent pipeline the failure (or success) occurred."""
        prompt = _render(
            "kb/classify_failure",
            stage_outputs=stage_outputs,
            shared_session=shared_session,
        )
        return await self._call_llm(prompt)

    async def _refine_diagnosis_heuristics(self, classification: str, stage_outputs: str) -> None:
        prior = self.diagnosis_heuristics_path.read_text() if self.diagnosis_heuristics_path.exists() else ""
        prompt = _render(
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
        prompt = _render(
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
        prompt = _render(
            "kb/refine_arbitration_heuristics",
            prior_guidance=prior,
            failure_classification=classification,
            stage_outputs=stage_outputs,
        )
        result = await self._call_llm(prompt)
        self.arbitration_heuristics_path.write_text(result)
        logger.info(f"Arbitration heuristics updated at {self.arbitration_heuristics_path}")

    async def _refine_heuristics(self, stage_outputs_file: Path | None = None) -> None:
        """Two-stage heuristic refinement: classify failure, then targeted edits."""
        stage_outputs = ""
        if stage_outputs_file and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text().strip()

        shared_session_parts = [sf.read_text() for sf in self.shared_files if sf.exists()]
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

        # Determine which heuristics need updating
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

    async def update(self, stage_outputs_file: Path | None = None) -> None:
        """Summarize the completed session and update the knowledge base."""
        parts = [sf.read_text() for sf in self.shared_files if sf.exists()]
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
            await self._refine_heuristics(stage_outputs_file)
        else:
            logger.info("Heuristic refinement disabled; skipping.")


# Backward-compatibility alias
CrucibleKnowledgeBase = StructuredKnowledgeBase

KB_APPEND_FILENAME = "knowledge.md"


class AppendOnlyKnowledgeBase(KnowledgeBase):
    """Simple append-only knowledge base that stores session summaries in a single file."""

    def __init__(
        self,
        shared_files: list[Path],
        kb_dir: Path,
        model_id: str,
        app_name: str = "unknown",
        seed_kb_dir: Path | None = None,
        include_benchmark_results: bool = False,
        enable_heuristic_refinement: bool = True,
        include_incident_files: bool = True,
    ):
        self.shared_files = shared_files
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.app_name = app_name
        self.include_benchmark_results = include_benchmark_results

    @property
    def knowledge_path(self) -> Path:
        return self.kb_dir / KB_APPEND_FILENAME

    async def inject(self, target_dir: Path) -> InjectedKB:
        result = InjectedKB()
        if self.knowledge_path.exists():
            dest = target_dir / KB_APPEND_FILENAME
            shutil.copy2(self.knowledge_path, dest)
            result.summary = dest
            logger.info(f"Knowledge base: copied append-only KB to {dest}")
        else:
            logger.info("Knowledge base: no prior knowledge file; starting fresh.")
        return result

    async def extract_triage_additions(self) -> str:
        return ""

    async def _call_llm(self, prompt: str) -> str:
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await agent.run(prompt)
        return result.output

    async def update(self, stage_outputs_file: Path | None = None) -> None:
        parts = [sf.read_text() for sf in self.shared_files if sf.exists()]
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

        logger.info("Generating session summary for append-only KB...")
        try:
            session_summary = await self._summarize_session(content)
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n\n---\n\n## {timestamp}\n\n{session_summary}"

        with open(self.knowledge_path, "a") as f:
            f.write(entry)
        logger.info(f"Appended session summary to {self.knowledge_path}")

    async def _summarize_session(self, content: str) -> str:
        prompt = _render(
            "kb/summarize_session",
            content=content,
            include_benchmark_results=self.include_benchmark_results,
        )
        return await self._call_llm(prompt)


def create_knowledge_base(
    kb_type: str,
    shared_files: list[Path],
    kb_dir: Path,
    model_id: str,
    app_name: str = "unknown",
    seed_kb_dir: Path | None = None,
    include_benchmark_results: bool = False,
    enable_heuristic_refinement: bool = True,
    include_incident_files: bool = True,
) -> KnowledgeBase:
    """Factory function to create a knowledge base implementation."""
    if kb_type == "structured":
        return StructuredKnowledgeBase(
            shared_files,
            kb_dir,
            model_id,
            app_name,
            seed_kb_dir,
            include_benchmark_results=include_benchmark_results,
            enable_heuristic_refinement=enable_heuristic_refinement,
            include_incident_files=include_incident_files,
        )
    if kb_type == "append-only":
        return AppendOnlyKnowledgeBase(
            shared_files,
            kb_dir,
            model_id,
            app_name,
            include_benchmark_results=include_benchmark_results,
            enable_heuristic_refinement=enable_heuristic_refinement,
            include_incident_files=include_incident_files,
        )
    raise ValueError(f"Unknown kb_type: {kb_type!r}. Must be 'structured' or 'append-only'.")
