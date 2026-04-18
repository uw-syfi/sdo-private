"""Shared base types for the crucible knowledge base."""

from __future__ import annotations

import abc
import dataclasses
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


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


def sanitize_app_name(name: str) -> str:
    """Sanitize an application name for use as a directory name."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_").lower() or "unknown"


class KnowledgeBase(abc.ABC):
    """Abstract base class for knowledge base implementations.

    KB curation follows the v3 pipeline: the crucible driver writes incident
    records (diagnosis/mitigation runs and playbook candidates) into the KB
    directory, and an out-of-band ``kb_worker`` process consumes those records
    to produce/merge root-cause playbooks. KB implementations therefore only
    need to expose :meth:`inject` so agents can read the current KB view;
    updates are driven by the incident-record files on disk, not by an
    in-process method call.
    """

    @abc.abstractmethod
    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy KB files into target_dir for agent consumption."""
