"""Protocol for running a single SDS agent phase on a repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class AgentRunner(Protocol):
    """Runs one SDS agent phase on a repository."""

    def run(
        self,
        repo_path: Path,
        agent: Any,
        filesystem: Any,
        recorder: Any,
    ) -> None: ...
