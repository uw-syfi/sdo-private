"""Structured knowledge base with root-cause-first indexing and reflection."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from .base import (
    MAX_CITATION_RETRIES,
    MAX_INJECTED_INCIDENTS,
    InjectedKB,
    KnowledgeBase,
    SessionFiles,
    find_invalid_citations,
    find_invalid_citations_unified,
    sanitize_app_name,
    strip_benchmark_result,
    strip_citation_wrappers,
)
from .merge_result import (
    MergeEnvelopeError,
    MergeResult,
    Reorganization,
    build_merge_result,
    ensure_slug_lines,
    extract_class_slugs,
    parse_merge_envelope,
)
from .playbook import Playbook, PlaybookStore
from .playbook_synthesizer import PlaybookSynthesizer
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
    from sregym_agents.crucible.recovery_reflection import RecoveryReflection

logger = logging.getLogger(__name__)

MAX_MERGE_ENVELOPE_RETRIES = 2


def _extract_oracle_answer(stage_outputs: str) -> str:
    """Best-effort extraction of the oracle's correct answer from a stage_outputs file.

    The benchmark embeds the oracle inside ``<oracle>...</oracle>`` blocks within
    ``<benchmark_result>``. Returns an empty string if no oracle text is found.
    """
    if not stage_outputs:
        return ""
    open_tag = "<oracle>"
    close_tag = "</oracle>"
    pieces: list[str] = []
    idx = 0
    while True:
        start = stage_outputs.find(open_tag, idx)
        if start == -1:
            break
        end = stage_outputs.find(close_tag, start)
        if end == -1:
            break
        pieces.append(stage_outputs[start + len(open_tag) : end].strip())
        idx = end + len(close_tag)
    return "\n\n---\n\n".join(pieces) if pieces else ""


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
        self.app_dir = self.kb_dir / sanitize_app_name(self.app_name)
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.include_benchmark_results = config.include_benchmark_results
        self.enable_reflection = config.enable_reflection
        self.enable_playbooks = config.enable_playbooks
        self.include_incident_files = config.include_incident_files
        self.per_app = config.per_app
        self.prompts = renderer
        self.schema = SCHEMA_V2
        self._reflector = Reflector(self.kb_dir, model_id, renderer)

        self._playbook_store: PlaybookStore | None = None
        self._playbook_synthesizer: PlaybookSynthesizer | None = None
        if self.enable_playbooks:
            self._playbook_store = PlaybookStore(self.kb_dir / "playbooks")
            self._playbook_synthesizer = PlaybookSynthesizer(model_id, renderer)

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
        sanitized = sanitize_app_name(self.app_name)
        seed_app_dir = seed_kb_dir / sanitized

        # Per-app summary (try per-app dir first, fall back to root for unified seed)
        seed_summary = seed_app_dir / seed_schema.summary
        if not seed_summary.exists():
            seed_summary = seed_kb_dir / seed_schema.summary
        if not self.summary_path.exists() and seed_summary.exists():
            shutil.copy2(seed_summary, self.summary_path)
            logger.info(f"Seeded summary from {seed_summary}")

        # Per-app incidents (try per-app dir first, fall back to unified layout)
        seed_incidents = seed_app_dir / seed_schema.incidents_dir
        if not seed_incidents.is_dir():
            seed_incidents = seed_kb_dir / seed_schema.incidents_dir / sanitized
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
        for prior_field in ("diagnosis_priors", "triage_priors", "arbitration_priors", "verification_priors"):
            seed_filename = getattr(seed_schema, prior_field, "")
            if not seed_filename:
                continue
            dest_path = self.kb_dir / getattr(self.schema, prior_field)
            seed_file = seed_kb_dir / seed_filename
            if not dest_path.exists() and seed_file.exists():
                shutil.copy2(seed_file, dest_path)
                logger.info(f"Seeded {seed_filename} -> {dest_path.name}")

    @property
    def summary_path(self) -> Path:
        if self.per_app:
            return self.app_dir / self.schema.summary
        return self.kb_dir / self.schema.summary

    @property
    def lessons_path(self) -> Path:
        return self.kb_dir / self.schema.lessons

    @property
    def architecture_path(self) -> Path:
        return self.app_dir / self.schema.architecture

    @property
    def incidents_dir(self) -> Path:
        if self.per_app:
            return self.app_dir / self.schema.incidents_dir
        return self.kb_dir / self.schema.incidents_dir / sanitize_app_name(self.app_name)

    @property
    def diagnosis_priors_path(self) -> Path:
        return self.kb_dir / self.schema.diagnosis_priors

    @property
    def triage_priors_path(self) -> Path:
        return self.kb_dir / self.schema.triage_priors

    @property
    def arbitration_priors_path(self) -> Path:
        return self.kb_dir / self.schema.arbitration_priors

    @property
    def verification_priors_path(self) -> Path:
        return self.kb_dir / self.schema.verification_priors

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

        if self.include_incident_files and self.per_app and self.incidents_dir.is_dir():
            incident_files = sorted(self.incidents_dir.glob("*.md"))[-MAX_INJECTED_INCIDENTS:]
            if incident_files:
                dest_incidents = target_dir / self.schema.incidents_dir
                dest_incidents.mkdir(exist_ok=True)
                for f in incident_files:
                    shutil.copy2(f, dest_incidents / f.name)
                result.incidents_dir = dest_incidents
                logger.info(f"Knowledge base: copied {len(incident_files)} incident(s) to {dest_incidents}")
        elif self.include_incident_files and not self.per_app:
            root_incidents = self.kb_dir / self.schema.incidents_dir
            if root_incidents.is_dir():
                all_incident_files = sorted(root_incidents.glob("*/*.md"))[-MAX_INJECTED_INCIDENTS:]
                if all_incident_files:
                    for f in all_incident_files:
                        dest_sub = target_dir / self.schema.incidents_dir / f.parent.name
                        dest_sub.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest_sub / f.name)
                    result.incidents_dir = target_dir / self.schema.incidents_dir
                    logger.info(
                        f"Knowledge base: copied {len(all_incident_files)} incident(s) to {result.incidents_dir}"
                    )
        elif not self.include_incident_files:
            logger.info("Knowledge base: incident file injection disabled by include_incident_files=false")

        # Prior files (root-level, cross-app)
        for prior_field in ("diagnosis_priors", "triage_priors", "arbitration_priors", "verification_priors"):
            filename = getattr(self.schema, prior_field)
            src = self.kb_dir / filename
            if src.exists():
                dest = target_dir / filename
                shutil.copy2(src, dest)
                setattr(result, prior_field, dest)
                logger.info(f"Knowledge base: copied {filename} to {dest}")

        # Playbooks directory (excludes .history/, includes .aliases.yaml)
        if self.enable_playbooks:
            src_playbooks = self.kb_dir / "playbooks"
            if src_playbooks.is_dir():
                dest_playbooks = target_dir / "playbooks"
                dest_playbooks.mkdir(exist_ok=True)
                copied = 0
                for entry in src_playbooks.iterdir():
                    if entry.name == ".history":
                        continue
                    if entry.is_dir():
                        continue
                    shutil.copy2(entry, dest_playbooks / entry.name)
                    copied += 1
                if copied:
                    result.playbooks_dir = dest_playbooks
                    logger.info(f"Knowledge base: copied {copied} playbook file(s) to {dest_playbooks}")

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
    ) -> MergeResult:
        migrated_prior = ensure_slug_lines(prior_summary)
        pre_merge_slugs = extract_class_slugs(migrated_prior)
        existing_slugs = sorted(set(pre_merge_slugs.values()))

        prompt = self.prompts.render(
            "kb/merge_summary",
            session_summary=session_summary,
            prior_summary=migrated_prior,
            incident_ref=incident_ref,
            existing_slugs=existing_slugs,
        )
        agent: Agent[None, str] = Agent(self.model_id, output_type=str)
        result = await arun_with_retry(agent, prompt)
        output = result.output

        for attempt in range(MAX_CITATION_RETRIES):
            if self.per_app:
                invalid = find_invalid_citations(output, self.incidents_dir)
            else:
                invalid = find_invalid_citations_unified(output, self.kb_dir)
            if not invalid:
                break
            valid_files = sorted(f.name for f in self.incidents_dir.glob("*.md")) if self.incidents_dir.is_dir() else []
            if self.per_app:
                fmt_hint = "Use the exact format {{ref:incidents/FILENAME.md}} for each citation."
            else:
                app_slug = sanitize_app_name(self.app_name)
                fmt_hint = f"Use the exact format {{{{ref:incidents/{app_slug}/FILENAME.md}}}} for each citation."
            correction = (
                f"The following incident citations are invalid (files do not exist): {invalid}\n"
                f"Valid incident files are: {valid_files}\n"
                f"Please output the COMPLETE updated Long-Term Summary again with corrected citations. "
                f"{fmt_hint}"
            )
            logger.warning(f"Citation validation failed (attempt {attempt + 1}/{MAX_CITATION_RETRIES}): {invalid}")
            result = await arun_with_retry(agent, correction, message_history=result.all_messages())
            output = result.output

        last_error: MergeEnvelopeError | None = None
        for attempt in range(MAX_MERGE_ENVELOPE_RETRIES):
            try:
                envelope, body = parse_merge_envelope(output)
                migrated_body = ensure_slug_lines(body)
                post_merge_slugs = extract_class_slugs(migrated_body)
                merge_result = build_merge_result(
                    envelope,
                    pre_merge_slugs=pre_merge_slugs,
                    post_merge_slugs=post_merge_slugs,
                    new_summary_text=strip_citation_wrappers(migrated_body),
                )
                return merge_result
            except MergeEnvelopeError as exc:
                last_error = exc
                if attempt >= MAX_MERGE_ENVELOPE_RETRIES - 1:
                    break
                logger.warning(
                    f"Merge envelope invalid (attempt {attempt + 1}/{MAX_MERGE_ENVELOPE_RETRIES}): {exc}; "
                    "retrying with correction"
                )
                correction = (
                    f"Your merge_result envelope was rejected by the validator: {exc}\n"
                    "Re-emit the COMPLETE updated Long-Term Summary followed by a valid "
                    "<merge_result>...</merge_result> envelope. Do not drop any existing slugs "
                    "unless you declare them as `consolidate` losers."
                )
                result = await arun_with_retry(agent, correction, message_history=result.all_messages())
                output = result.output

        logger.warning(
            f"Merge envelope validation failed after {MAX_MERGE_ENVELOPE_RETRIES} attempts: {last_error}. "
            "Falling back to no-op merge with pre-merge summary."
        )
        return MergeResult(
            primary_action="noop",
            primary_slug=None,
            primary_class_name=None,
            new_summary_text=strip_citation_wrappers(migrated_prior),
        )

    async def _extract_operational_lessons(self, long_term_summary: str) -> str:
        prompt = self.prompts.render("kb/extract_lessons", long_term_summary=long_term_summary)
        return await self._call_llm(prompt)

    async def _distill_lessons(self) -> None:
        """Re-distill cross-cutting operational lessons from all per-app summaries."""
        if self.per_app:
            summary_files = sorted(self.kb_dir.glob(f"*/{self.schema.summary}"))
        else:
            root_summary = self.kb_dir / self.schema.summary
            summary_files = [root_summary] if root_summary.exists() else []
        if not summary_files:
            logger.info(f"No {self.schema.summary} files found; skipping lessons extraction.")
            return

        parts: list[str] = []
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

    async def _run_playbook_lifecycle(
        self,
        *,
        merge_result: MergeResult,
        diagnosis_succeeded: bool,
        stage_outputs_file: Path | None,
        recovery_reflection: RecoveryReflection | None,
        incident_ref: str,
    ) -> None:
        """Synthesize, refine, or consolidate playbooks based on the merge outcome."""
        if self._playbook_store is None or self._playbook_synthesizer is None:
            return

        stage_outputs = ""
        if stage_outputs_file is not None and stage_outputs_file.exists():
            stage_outputs = stage_outputs_file.read_text()

        oracle_answer = _extract_oracle_answer(stage_outputs)

        for reorg in merge_result.reorganizations:
            try:
                await self._handle_reorganization(
                    reorg,
                    stage_outputs=stage_outputs,
                    recovery_reflection=recovery_reflection,
                    incident_ref=incident_ref,
                )
            except Exception as e:
                logger.error(f"Playbook reorganization {reorg.type} failed: {e}", exc_info=True)

        if merge_result.primary_action == "noop" or merge_result.primary_slug is None:
            logger.info("Playbook lifecycle: primary action is noop; nothing to synthesize.")
            return

        slug = merge_result.primary_slug
        class_name = merge_result.primary_class_name or slug
        existing = self._playbook_store.load(slug)

        if existing is None:
            if diagnosis_succeeded:
                logger.info(f"Playbook lifecycle: synthesizing new playbook from success for slug={slug}")
                pb = await self._playbook_synthesizer.synthesize_from_success(
                    class_name=class_name,
                    slug=slug,
                    stage_outputs=stage_outputs,
                    oracle_answer=oracle_answer,
                    incident_ref=incident_ref,
                )
            elif recovery_reflection is not None:
                logger.info(f"Playbook lifecycle: synthesizing new playbook from recovery for slug={slug}")
                pb = await self._playbook_synthesizer.synthesize_from_recovery(
                    class_name=class_name,
                    slug=slug,
                    stage_outputs=stage_outputs,
                    recovery_reflection=recovery_reflection,
                    oracle_answer=oracle_answer,
                    incident_ref=incident_ref,
                )
            else:
                logger.warning(
                    f"Playbook lifecycle: new class {slug!r} but diagnosis failed and no recovery available; skipping"
                )
                return
        else:
            if diagnosis_succeeded:
                logger.info(f"Playbook lifecycle: existing playbook {slug!r} succeeded; no-op")
                return
            if recovery_reflection is None:
                logger.warning(
                    f"Playbook lifecycle: existing playbook {slug!r} failed but no recovery available; cannot refine"
                )
                return
            logger.info(f"Playbook lifecycle: refining playbook for slug={slug}")
            pb = await self._playbook_synthesizer.refine(
                existing=existing,
                stage_outputs=stage_outputs,
                recovery_reflection=recovery_reflection,
                oracle_answer=oracle_answer,
                incident_ref=incident_ref,
            )

        if pb is None:
            logger.warning(f"Playbook lifecycle: synthesizer returned None for slug={slug}; skipping save")
            return

        self._playbook_store.save(pb)

    async def _handle_reorganization(
        self,
        reorg: Reorganization,
        *,
        stage_outputs: str,
        recovery_reflection: RecoveryReflection | None,
        incident_ref: str,
    ) -> None:
        """Apply a single reorganization (consolidate or split) to the playbook store."""
        assert self._playbook_store is not None
        assert self._playbook_synthesizer is not None

        if reorg.type == "split":
            logger.warning(f"Split reorganization not supported in v1; aliasing children to parent {reorg.winner_slug}")
            for child_slug in reorg.loser_slugs:
                if child_slug != reorg.winner_slug:
                    self._playbook_store.add_alias(child_slug, reorg.winner_slug)
            return

        if reorg.type != "consolidate":
            logger.warning(f"Unknown reorganization type: {reorg.type}")
            return

        winner_slug = reorg.winner_slug
        winner_class_name = reorg.winner_class_name or winner_slug

        winner_pb = self._playbook_store.load(winner_slug)
        loser_pbs: list[Playbook] = []
        for loser_slug in reorg.loser_slugs:
            lp = self._playbook_store.load(loser_slug)
            if lp is not None:
                loser_pbs.append(lp)

        if winner_pb is None and not loser_pbs:
            logger.info(
                f"Consolidation declared for {winner_slug} but no playbooks exist yet; writing alias-only entries"
            )
            for loser_slug in reorg.loser_slugs:
                if loser_slug != winner_slug:
                    self._playbook_store.add_alias(loser_slug, winner_slug)
            return

        combined_seen = (winner_pb.seen if winner_pb else 0) + sum(lp.seen for lp in loser_pbs)
        combined_refs: dict[str, list[str]] = {"successes": [], "failures": []}
        if winner_pb is not None:
            combined_refs["successes"].extend(winner_pb.references.get("successes", []))
            combined_refs["failures"].extend(winner_pb.references.get("failures", []))
        for lp in loser_pbs:
            combined_refs["successes"].extend(lp.references.get("successes", []))
            combined_refs["failures"].extend(lp.references.get("failures", []))
        # Deduplicate while preserving order.
        for key in ("successes", "failures"):
            seen: set[str] = set()
            unique: list[str] = []
            for ref in combined_refs[key]:
                if ref not in seen:
                    seen.add(ref)
                    unique.append(ref)
            combined_refs[key] = unique

        recovery_summary = recovery_reflection.summary if recovery_reflection is not None else ""

        consolidated = await self._playbook_synthesizer.consolidate(
            winner_class_name=winner_class_name,
            winner_slug=winner_slug,
            winner_playbook=winner_pb,
            loser_playbooks=loser_pbs,
            combined_seen=combined_seen,
            stage_outputs=stage_outputs,
            recovery_summary=recovery_summary,
            combined_references=combined_refs,
        )

        if consolidated is None:
            logger.warning(
                f"Consolidation synthesizer returned None for winner={winner_slug}; "
                "leaving inputs intact and writing aliases only"
            )
            for loser_slug in reorg.loser_slugs:
                if loser_slug != winner_slug:
                    self._playbook_store.add_alias(loser_slug, winner_slug)
            return

        self._playbook_store.save(consolidated)

        for loser_slug in reorg.loser_slugs:
            if loser_slug == winner_slug:
                continue
            self._playbook_store.archive_consolidation(loser_slug, winner_slug)
            self._playbook_store.add_alias(loser_slug, winner_slug)

    async def update(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
        recovery_reflection: RecoveryReflection | dict[str, Any] | None = None,
        diagnosis_succeeded: bool = False,
    ) -> None:
        """Summarize the completed session and update the knowledge base."""
        parts = session_files.read_all()
        if not parts:
            logger.warning("No shared files found; skipping knowledge base update.")
            return

        raw = "\n\n".join(parts)
        if self.include_benchmark_results:
            content = raw.strip()
        else:
            content = strip_benchmark_result(raw)
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
            if self.per_app:
                incident_ref = f"incidents/{incident_id}.md"
            else:
                incident_ref = f"incidents/{sanitize_app_name(self.app_name)}/{incident_id}.md"
        else:
            logger.info("Skipping incident file save (include_incident_files=false)")

        prior_summary = self.summary_path.read_text() if self.summary_path.exists() else ""
        logger.info("Merging into long-term summary...")
        try:
            merge_result = await self._merge_into_long_term_summary(
                session_summary, prior_summary, incident_ref=incident_ref
            )
        except Exception as e:
            logger.error(f"Failed to merge into long-term summary: {e}")
            return

        self.summary_path.write_text(merge_result.new_summary_text)
        logger.info(
            f"Long-term summary updated at {self.summary_path} "
            f"(primary_action={merge_result.primary_action}, primary_slug={merge_result.primary_slug})"
        )

        normalized_recovery_reflection: RecoveryReflection | None = None
        if recovery_reflection is not None:
            from sregym_agents.crucible.recovery_reflection import RecoveryReflection as _RecoveryReflection

            normalized_recovery_reflection = (
                recovery_reflection
                if isinstance(recovery_reflection, _RecoveryReflection)
                else _RecoveryReflection.model_validate(recovery_reflection)
            )

        if self.enable_playbooks:
            try:
                await self._run_playbook_lifecycle(
                    merge_result=merge_result,
                    diagnosis_succeeded=diagnosis_succeeded,
                    stage_outputs_file=stage_outputs_file,
                    recovery_reflection=normalized_recovery_reflection,
                    incident_ref=incident_ref,
                )
            except Exception as e:
                logger.error(f"Playbook lifecycle failed: {e}", exc_info=True)

        await self._distill_lessons()
        if self.enable_reflection:
            await self._reflector.run(
                stage_outputs_file=stage_outputs_file,
                recovery_reflection=normalized_recovery_reflection,
            )
        else:
            logger.info("Reflection disabled; skipping.")
