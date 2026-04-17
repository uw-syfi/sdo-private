"""Shared base types for the crucible knowledge base."""

from __future__ import annotations

import abc
import dataclasses
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from libs.pydantic_agent import UsageCollector


@dataclasses.dataclass
class InjectedKB:
    """Paths to KB files injected into the experiment environment."""

    kb_view_dir: Path | None = None
    diagnosis_priors: Path | None = None
    triage_priors: Path | None = None
    arbitration_priors: Path | None = None
    verification_priors: Path | None = None
    architecture: Path | None = None

    def get_view(self):
        """Build a KB view for the injected scope."""
        if self.kb_view_dir is None:
            return None
        from .root_cause import KBView

        return KBView(self.kb_view_dir)


@dataclasses.dataclass
class SessionFiles:
    """Session transcript files produced during a crucible run."""

    diagnosis: Path | None = None
    mitigation: Path | None = None

    def read_all(self) -> list[str]:
        """Read content from all existing session files."""
        files = [f for f in (self.diagnosis, self.mitigation) if f is not None]
        return [f.read_text() for f in files if f.exists()]


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
        diagnosis_succeeded: bool = False,
        mitigation_succeeded: bool = False,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        """Update the knowledge base from the completed session."""
