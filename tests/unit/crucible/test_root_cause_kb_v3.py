from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.knowledge_base.root_cause import (
    DiagnosisFrontMatter,
    DiagnosisPlaybook,
    MitigationFrontMatter,
    MitigationPlaybook,
    RootCauseStore,
)
from sregym_agents.crucible.knowledge_base.structured import StructuredKnowledgeBase

if TYPE_CHECKING:
    from pathlib import Path


class _DummyDriver(AgentDriver):
    async def run(self, **kwargs) -> AgentResult[str]:
        return AgentResult(output="")


def _diagnosis_playbook(slug: str = "wrong_port") -> DiagnosisPlaybook:
    return DiagnosisPlaybook(
        front_matter=DiagnosisFrontMatter(
            slug=slug,
            root_cause="A client deployment points at the wrong upstream port.",
            when_to_consider=["Internal service call fails while upstream service looks healthy."],
            disambiguators=["Not a DNS failure."],
        ),
        summary="Verify whether a client env var uses the wrong upstream port.",
        triage_checks=["1. Inspect failing client pods and recent errors."],
        verification_checks=["1. Compare the client env var port with the Service port."],
        required_evidence=["Client points to the wrong upstream port.", "Service exposes a different port."],
        known_confounders=["Service targetPort mismatch."],
    )


def _mitigation_playbook(slug: str = "wrong_port") -> MitigationPlaybook:
    return MitigationPlaybook(
        front_matter=MitigationFrontMatter(
            slug=slug,
            root_cause="A client deployment points at the wrong upstream port.",
        ),
        summary="Correct the client port configuration and verify recovery.",
        mitigation_procedure=["1. Patch the client deployment to use the correct Service port."],
        verification_checks=["1. Confirm the client can reach the upstream service successfully."],
        rollback_stop_conditions=["Stop if the correct Service port cannot be determined confidently."],
    )


def test_root_cause_store_round_trips_playbooks(tmp_path: Path):
    store = RootCauseStore(tmp_path / "scope")
    store.save_diagnosis(_diagnosis_playbook(), created_from="problem-a")
    store.save_mitigation(_mitigation_playbook(), created_from="problem-a")

    manifest = store.load_manifest()
    entry = manifest.entry_for("wrong_port")
    assert entry is not None
    assert entry.has_diagnosis is True
    assert entry.has_mitigation is True

    loaded_diag = store.load_diagnosis("wrong_port")
    loaded_mit = store.load_mitigation("wrong_port")
    assert loaded_diag is not None
    assert loaded_mit is not None
    assert loaded_diag.front_matter.root_cause == "A client deployment points at the wrong upstream port."
    assert loaded_mit.summary == "Correct the client port configuration and verify recovery."


@pytest.mark.asyncio
async def test_structured_kb_injects_manifest_and_root_causes(tmp_path: Path):
    kb = StructuredKnowledgeBase(
        tmp_path / "kb",
        app_name="hotel-reservation",
        config=CrucibleConfig(prompt_version="v3", kb_scope="per_app"),
        renderer=PromptRenderer("v3"),
        driver=_DummyDriver(),
    )
    kb.store.save_diagnosis(_diagnosis_playbook(), created_from="problem-a")
    kb.write_scope_metadata()

    target = tmp_path / "target"
    target.mkdir()
    injected = await kb.inject(target)

    assert injected.kb_view_dir is not None
    manifest = json.loads((injected.kb_view_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == 3
    assert (injected.kb_view_dir / "root_causes" / "wrong_port" / "diagnosis.md").exists()
    kb_view = injected.get_view()
    assert kb_view is not None
    assert kb_view.load_diagnosis("wrong_port") is not None


def test_structured_kb_writes_incident_records(tmp_path: Path):
    kb = StructuredKnowledgeBase(
        tmp_path / "kb",
        app_name="hotel-reservation",
        config=CrucibleConfig(prompt_version="v3", kb_scope="per_app"),
        renderer=PromptRenderer("v3"),
        driver=_DummyDriver(),
    )
    original, grounded = kb.write_incident_records(
        timestamp="20260413_162613",
        original_run_md="# Original",
        grounded_run_md="# Grounded",
    )
    assert original.read_text() == "# Original"
    assert grounded.read_text() == "# Grounded"
    assert original.parent.name == "20260413_162613"
