"""Dependency injection types and shared state for Crucible agents."""

from __future__ import annotations

import fcntl
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sregym_agents.crucible._prompts import PromptRenderer

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.models import Model

    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible.agents.base import RunSubagent
    from sregym_agents.crucible.config import CrucibleConfig
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
    reflection: str = Field(
        default="",
        description=(
            "Recovery only: 2-3 sentence analysis of why the original agent's "
            "diagnosis or mitigation was wrong — what investigative steps were missed "
            "or what evidence was misinterpreted, and what lesson follows. "
            "Leave empty for normal diagnosis and mitigation stages."
        ),
    )
    message_history: list[Any] = Field(
        default_factory=list,
        exclude=True,
        description="Internal only: captured agent message history for follow-on phases.",
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
        with self._path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(text)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self) -> str:
        return self._path.read_text()

    def read_text(self) -> str:
        return self._path.read_text()

    def write_text(self, text: str) -> None:
        self._path.write_text(text)

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
    answer_reflection: str | None = None
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
    renderer: PromptRenderer = field(default_factory=lambda: PromptRenderer("v1"))
    state: SharedState = field(default_factory=SharedState)
    config: CrucibleConfig | None = None
    lt_summary_file: Path | None = None
    incidents_dir: Path | None = None
    playbooks_dir: Path | None = None
    mitigation_playbooks_dir: Path | None = None
    ltm_call_count: int = 0
    ltm_call_budget: int = 1
    triage_report: TriageReport | None = None
    # v3 trained guidance
    triage_priors: TriagePriors | None = None
    verification_guidance: str = ""
    stage_outputs_file: Path | None = None
    usage_collector: UsageCollector | None = None
    run_subagent: RunSubagent | None = None

    @property
    def enable_ltm_verified_direct_submit(self) -> bool:
        """Shorthand -- reads the flag from the embedded CrucibleConfig."""
        return self.config.enable_ltm_verified_direct_submit if self.config else False


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
