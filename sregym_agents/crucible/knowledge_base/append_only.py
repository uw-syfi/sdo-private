"""Append-only knowledge base implementation."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import KB_APPEND_FILENAME, InjectedKB, KnowledgeBase, SessionFiles, strip_benchmark_result

if TYPE_CHECKING:
    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver
    from sregym_agents.crucible.config import CrucibleConfig
    from sregym_agents.crucible.recovery_reflection import RecoveryReflection

logger = logging.getLogger(__name__)


class AppendOnlyKnowledgeBase(KnowledgeBase):
    """Simple append-only knowledge base that stores session summaries in a single file."""

    def __init__(
        self,
        kb_dir: Path,
        app_name: str = "unknown",
        *,
        config: CrucibleConfig | None = None,
        renderer: PromptRenderer,
        driver: AgentDriver,
    ):
        from sregym_agents.crucible.config import CrucibleConfig as _CrucibleConfig

        if config is None:
            config = _CrucibleConfig()
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name
        self.include_benchmark_results = config.include_benchmark_results
        self.prompts = renderer
        self._usage_collector: UsageCollector | None = None
        self._driver = driver

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

    async def _call_llm(self, prompt: str) -> str:
        result = await self._driver.run(
            prompt=prompt,
            output_type=str,
            agent_name="kb-append-only",
            usage_collector=self._usage_collector,
        )
        return result.output or ""

    async def update(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
        recovery_reflection: RecoveryReflection | dict[str, Any] | None = None,
        diagnosis_succeeded: bool = False,
        mitigation_succeeded: bool = False,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        self._usage_collector = usage_collector
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
