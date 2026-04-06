"""Append-only knowledge base implementation."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from libs.agent_mw import arun_with_retry

from .base import KB_APPEND_FILENAME, InjectedKB, KnowledgeBase, SessionFiles, _strip_benchmark_result

if TYPE_CHECKING:
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.orchestrator import CrucibleFlags

logger = logging.getLogger(__name__)


class AppendOnlyKnowledgeBase(KnowledgeBase):
    """Simple append-only knowledge base that stores session summaries in a single file."""

    def __init__(
        self,
        kb_dir: Path,
        model_id: str,
        app_name: str = "unknown",
        *,
        flags: CrucibleFlags | None = None,
        renderer: PromptRenderer,
    ):
        from sregym_agents.crucible.orchestrator import CrucibleFlags as _CrucibleFlags

        if flags is None:
            flags = _CrucibleFlags()
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self.app_name = app_name
        self.include_benchmark_results = flags.include_benchmark_results
        self.prompts = renderer

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
        result = await arun_with_retry(agent, prompt)
        return result.output

    async def update(self, session_files: SessionFiles, stage_outputs_file: Path | None = None) -> None:
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
        prompt = self.prompts.render(
            "kb/summarize_session",
            content=content,
            include_benchmark_results=self.include_benchmark_results,
        )
        return await self._call_llm(prompt)
