"""Root-cause-first structured knowledge base."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .base import InjectedKB, KnowledgeBase, SessionFiles, sanitize_app_name
from .root_cause import RootCauseStore

if TYPE_CHECKING:
    from libs.pydantic_agent import UsageCollector
    from sregym_agents.crucible._prompts import PromptRenderer
    from sregym_agents.crucible.agents.base import AgentDriver
    from sregym_agents.crucible.config import CrucibleConfig

logger = logging.getLogger(__name__)


class StructuredKnowledgeBase(KnowledgeBase):
    """Filesystem-backed root-cause KB with shared/per-app scopes."""

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

        self._config = config or _CrucibleConfig()
        self.kb_dir = Path(kb_dir)
        self.kb_root = self.kb_dir / "v3"
        self.kb_root.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name
        self.app_slug = sanitize_app_name(app_name)
        self.prompts = renderer
        self._driver = driver

        if self._config.kb_scope == "per_app":
            self.scope_dir = self.kb_root / "apps" / self.app_slug
        else:
            self.scope_dir = self.kb_root / "shared"
        self.scope_dir.mkdir(parents=True, exist_ok=True)

        self.store = RootCauseStore(self.scope_dir)
        self.store.refresh_manifest()

        reviews_root = self.kb_root / "reviews"
        for name in ("pending", "completed", "failed"):
            (reviews_root / name).mkdir(parents=True, exist_ok=True)

    async def inject(self, target_dir: Path) -> InjectedKB:
        """Copy one KB runtime scope into target_dir for runtime retrieval."""
        result = InjectedKB()

        self.store.refresh_manifest()
        dest_view = target_dir / "kb_view"
        if dest_view.exists():
            shutil.rmtree(dest_view)
        shutil.copytree(self.store.paths.scope_dir, dest_view)
        result.kb_view_dir = dest_view

        return result

    async def update(
        self,
        session_files: SessionFiles,
        stage_outputs_file: Path | None = None,
        diagnosis_succeeded: bool = False,
        mitigation_succeeded: bool = False,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        """Legacy no-op path.

        The v3 root-cause KB is updated by the dedicated KB worker from
        ``original_run.md`` and ``grounded_run.md`` incident records, not from
        inline session-summary merging.
        """
        logger.info(
            "StructuredKnowledgeBase.update() is unused in v3 root-cause mode; "
            "driver/kb_worker should write incident records and process review tasks instead."
        )

    def write_incident_records(
        self,
        *,
        timestamp: str,
        original_run_md: str,
        grounded_run_md: str,
    ) -> tuple[Path, Path]:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        original_path = incident_dir / "original_run.md"
        grounded_path = incident_dir / "grounded_run.md"
        original_path.write_text(original_run_md)
        grounded_path.write_text(grounded_run_md)
        return original_path, grounded_path

    def write_scope_metadata(self) -> None:
        self.store.paths.scope_dir.mkdir(parents=True, exist_ok=True)
        schema_path = self.kb_root / "kb_schema.json"
        schema_path.write_text(json.dumps({"schema_version": 3, "kind": "root_cause_playbooks"}, indent=2) + "\n")
