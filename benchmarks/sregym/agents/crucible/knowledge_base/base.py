"""Shared base types for the crucible knowledge base."""

from __future__ import annotations

import abc
import dataclasses
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from benchmarks.sregym.agents.crucible.recovery_reflection import RecoveryReflection
    from libs.pydantic_agent import UsageCollector

KB_APPEND_FILENAME = "knowledge.md"
MAX_INJECTED_INCIDENTS = 100


@dataclasses.dataclass
class InjectedKB:
    """Paths to KB files injected into the experiment environment."""

    summary: Path | None = None
    lessons: Path | None = None
    architecture: Path | None = None
    incidents_dir: Path | None = None
    diagnosis_priors: Path | None = None
    triage_priors: Path | None = None
    arbitration_priors: Path | None = None
    verification_priors: Path | None = None
    playbooks_dir: Path | None = None
    mitigation_playbooks_dir: Path | None = None


@dataclasses.dataclass
class SessionFiles:
    """Session transcript files produced during a crucible run."""

    diagnosis: Path | None = None
    mitigation: Path | None = None

    def read_all(self) -> list[str]:
        """Read content from all existing session files."""
        files = [f for f in (self.diagnosis, self.mitigation) if f is not None]
        return [f.read_text() for f in files if f.exists()]


BENCHMARK_RESULT_RE = re.compile(r"<benchmark_result>.*?</benchmark_result>", re.DOTALL)
CITATION_RE = re.compile(r"\{\{ref:(incidents/[^}]+)\}\}")
MAX_CITATION_RETRIES = 2


def strip_benchmark_result(text: str) -> str:
    """Remove all <benchmark_result>...</benchmark_result> blocks from text."""
    return BENCHMARK_RESULT_RE.sub("", text).strip()


def extract_citations(text: str) -> list[str]:
    """Extract all {{ref:incidents/...}} citation values from text."""
    result: list[str] = CITATION_RE.findall(text)
    return result


def find_invalid_citations(text: str, incidents_dir: Path) -> list[str]:
    """Return citation values that reference non-existent incident files."""
    citations = extract_citations(text)
    invalid: list[str] = []
    for ref in citations:
        # ref is like "incidents/20260324_010224.md"
        filename = Path(ref).name
        if not (incidents_dir / filename).exists():
            invalid.append(ref)
    return invalid


def find_invalid_citations_unified(text: str, kb_dir: Path) -> list[str]:
    """Return citation values that reference non-existent incident files (unified KB).

    In unified mode citations include the app subdirectory, e.g.
    ``incidents/myapp/20260324_010224.md``.  Resolve against *kb_dir* directly.
    """
    citations = extract_citations(text)
    return [ref for ref in citations if not (kb_dir / ref).exists()]


def strip_citation_wrappers(text: str) -> str:
    """Replace {{ref:incidents/foo.md}} with incidents/foo.md."""
    return CITATION_RE.sub(r"\1", text)


def sanitize_app_name(name: str) -> str:
    """Sanitize an application name for use as a directory name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_").lower() or "unknown"


class KnowledgeBase(abc.ABC):
    """Abstract base class for knowledge base implementations."""

    @abc.abstractmethod
    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy KB files into target_dir for agent consumption."""

    @abc.abstractmethod
    async def update(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
        recovery_reflection: RecoveryReflection | dict[str, Any] | None = None,
        diagnosis_succeeded: bool = False,
        mitigation_succeeded: bool = False,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        """Update the knowledge base from the completed session."""
