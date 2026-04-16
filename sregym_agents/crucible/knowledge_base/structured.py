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

    from .incident_review import DiagnosisPlaybookDraft, MitigationPlaybookDraft, TriageAreaCandidate

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

        triage_priors_src = self.scope_dir / "triage_priors.yaml"
        if triage_priors_src.exists():
            triage_priors_dest = target_dir / "triage_priors.yaml"
            shutil.copy2(triage_priors_src, triage_priors_dest)
            result.triage_priors = triage_priors_dest

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
        ``diagnosis_run.md`` and optional ``recovery_diagnosis_run.md`` incident
        records, not from
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
        diagnosis_run_md: str,
        recovery_diagnosis_run_md: str | None,
    ) -> tuple[Path, Path | None]:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        diagnosis_path = incident_dir / "diagnosis_run.md"
        diagnosis_path.write_text(diagnosis_run_md)

        recovery_path = None
        if recovery_diagnosis_run_md is not None:
            recovery_path = incident_dir / "recovery_diagnosis_run.md"
            recovery_path.write_text(recovery_diagnosis_run_md)
        return diagnosis_path, recovery_path

    def write_diagnosis_playbook_candidate(
        self,
        *,
        timestamp: str,
        draft: DiagnosisPlaybookDraft,
    ) -> Path:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = incident_dir / "diagnosis_playbook_candidate.json"
        candidate_path.write_text(json.dumps(draft.model_dump(mode="python"), indent=2) + "\n")
        return candidate_path

    def write_mitigation_records(
        self,
        *,
        timestamp: str,
        mitigation_run_md: str,
        recovery_mitigation_run_md: str | None,
    ) -> tuple[Path, Path | None]:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        mitigation_path = incident_dir / "mitigation_run.md"
        mitigation_path.write_text(mitigation_run_md)

        recovery_path = None
        if recovery_mitigation_run_md is not None:
            recovery_path = incident_dir / "recovery_mitigation_run.md"
            recovery_path.write_text(recovery_mitigation_run_md)
        return mitigation_path, recovery_path

    def write_mitigation_playbook_candidate(
        self,
        *,
        timestamp: str,
        draft: MitigationPlaybookDraft,
    ) -> Path:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = incident_dir / "mitigation_playbook_candidate.json"
        candidate_path.write_text(json.dumps(draft.model_dump(mode="python"), indent=2) + "\n")
        return candidate_path

    def write_triage_area_candidate(
        self,
        *,
        timestamp: str,
        candidate: TriageAreaCandidate,
    ) -> Path:
        incidents_root = self.store.paths.incidents_dir
        if self._config.kb_scope == "per_app":
            incident_dir = incidents_root / timestamp
        else:
            incident_dir = incidents_root / self.app_slug / timestamp
        incident_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = incident_dir / "triage_area_candidate.json"
        candidate_path.write_text(json.dumps(candidate.model_dump(mode="python"), indent=2) + "\n")
        return candidate_path

    def write_scope_metadata(self) -> None:
        self.store.paths.scope_dir.mkdir(parents=True, exist_ok=True)
        schema_path = self.kb_root / "kb_schema.json"
        schema_path.write_text(json.dumps({"schema_version": 3, "kind": "root_cause_playbooks"}, indent=2) + "\n")
