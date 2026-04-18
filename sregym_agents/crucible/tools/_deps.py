"""Dependency injection types and shared state for Crucible agents."""

from __future__ import annotations

import fcntl
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from sregym_agents.crucible._prompts import PromptRenderer

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.models import Model

    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible.agents.base import RunSubagent
    from sregym_agents.crucible.config import CrucibleConfig
    from sregym_agents.crucible.knowledge_base.root_cause import KBView
    from sregym_agents.crucible.tools._kb_tools import TriagePriors, TriageReport

logger = logging.getLogger(__name__)


class SRESubmission(BaseModel):
    answer: str = Field(
        description=(
            "Concise diagnosis of the fault (diagnosis stage) or description of applied mitigation (mitigation stage)."
        ),
    )
    justification: str = Field(
        description="Evidence and reasoning supporting the answer.",
    )
    causal_chain: str = Field(
        default="",
        description=(
            "Full causal chain: misconfigured field → mechanism → observed symptom "
            "(diagnosis stage only). Leave empty for mitigation stage."
        ),
    )


class SharedFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def init(self, content: str) -> None:
        if self._path.exists():
            logger.warning("Shared file already exists, skipping init: %s", self._path)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.rename(self._path)

    def append(self, text: str) -> None:
        # IMPORTANT: re-seek to end under the lock before writing. A
        # concurrent write_text/replace on another FD may have truncated
        # the file after this FD was opened, invalidating the cached
        # append offset. Also flush() before releasing the lock so buffered
        # bytes reach the kernel while we still hold it.
        with self._path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0, 2)  # SEEK_END
                fh.write(text)
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self) -> str:
        return self._path.read_text()

    def read_text(self) -> str:
        return self._path.read_text()

    def write_text(self, text: str) -> None:
        # Open with r+ so we can hold an exclusive lock across truncate+write,
        # preventing concurrent append() calls from losing data. Create the
        # file if it does not exist yet.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            # A benign race here only results in an extra empty-file
            # creation; the real mutation happens under the lock below.
            self._path.touch()
        with self._path.open("r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                fh.truncate()
                fh.write(text)
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def replace(self, old: str, new: str, count: int = 1) -> bool:
        """Atomically read-modify-write under a single exclusive lock.

        Returns True if at least one replacement occurred.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()
        with self._path.open("r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                content = fh.read()
                if old not in content:
                    return False
                updated = content.replace(old, new, count)
                fh.seek(0)
                fh.truncate()
                fh.write(updated)
                fh.flush()
                return True
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def open(self, mode: str = "r"):
        return self._path.open(mode)

    def __str__(self) -> str:
        return str(self._path)


@dataclass
class SharedState:
    submitted: bool = False
    verdict: str | None = None  # "APPROVED" | "REJECTED" | None
    answer: str | None = None
    answer_justification: str | None = None
    answer_causal_chain: str | None = None
    independent_findings_submitted: bool = False
    hypothesis_revealed: bool = False
    benchmark_block: str = ""


@dataclass
class TriageDeps:
    namespace: str
    shared_file: Path


@dataclass
class SREDeps:
    namespace: str
    shared_file: SharedFile
    iteration: int
    stage: str  # "diagnosis" | "mitigation"
    model_id: Model | str
    diagnosis_shared_file: SharedFile | None = None
    renderer: PromptRenderer = field(default_factory=lambda: PromptRenderer("v1"))
    state: SharedState = field(default_factory=SharedState)
    config: CrucibleConfig | None = None
    kb_view_dir: Path | None = None
    ltm_call_count: int = 0
    ltm_call_budget: int = 1
    triage_report: TriageReport | None = None
    # v3 trained guidance
    triage_priors: TriagePriors | None = None
    verification_guidance: str = ""
    stage_outputs_file: Path | None = None
    usage_collector: UsageCollector | None = None
    run_subagent: RunSubagent | None = None
    hypothesis_verified: bool = False

    @property
    def enable_ltm_verified_direct_submit(self) -> bool:
        """Shorthand -- reads the flag from the embedded CrucibleConfig."""
        return self.config.enable_ltm_verified_direct_submit if self.config else False

    @property
    def kb_view(self) -> KBView | None:
        if self.kb_view_dir is None:
            return None
        from sregym_agents.crucible.knowledge_base.root_cause import KBView

        return KBView(self.kb_view_dir)


@dataclass
class JudgeDeps:
    namespace: str
    shared_file: SharedFile
    iteration: int
    stage: str
    submit_mcp_url: str
    renderer: PromptRenderer = field(default_factory=lambda: PromptRenderer("v1"))
    hypothesis_text: str = ""
    state: SharedState = field(default_factory=SharedState)
    usage_collector: UsageCollector | None = None
