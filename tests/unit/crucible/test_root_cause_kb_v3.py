from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.knowledge_base.incident_records import (
    DiagnosisRunRecord,
    MitigationRunRecord,
    RecoveryDiagnosisRunRecord,
    RecoveryMitigationRunRecord,
)
from sregym_agents.crucible.knowledge_base.incident_review import DiagnosisPlaybookDraft, MitigationPlaybookDraft
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
        fault_localization_checks=["1. Identify the failing request path and the upstream it depends on."],
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
    diagnosis_run, recovery_run = kb.write_incident_records(
        timestamp="20260413_162613",
        diagnosis_run_md="# Diagnosis",
        recovery_diagnosis_run_md="# Recovery",
    )
    assert diagnosis_run.name == "diagnosis_run.md"
    assert diagnosis_run.read_text() == "# Diagnosis"
    assert recovery_run is not None
    assert recovery_run.name == "recovery_diagnosis_run.md"
    assert recovery_run.read_text() == "# Recovery"
    assert diagnosis_run.parent.name == "20260413_162613"


def test_structured_kb_skips_optional_recovery_incident_record(tmp_path: Path):
    kb = StructuredKnowledgeBase(
        tmp_path / "kb",
        app_name="hotel-reservation",
        config=CrucibleConfig(prompt_version="v3", kb_scope="per_app"),
        renderer=PromptRenderer("v3"),
        driver=_DummyDriver(),
    )
    diagnosis_run, recovery_run = kb.write_incident_records(
        timestamp="20260413_162613",
        diagnosis_run_md="# Diagnosis",
        recovery_diagnosis_run_md=None,
    )

    assert diagnosis_run.name == "diagnosis_run.md"
    assert diagnosis_run.read_text() == "# Diagnosis"
    assert recovery_run is None
    assert not (diagnosis_run.parent / "recovery_diagnosis_run.md").exists()


def test_structured_kb_writes_diagnosis_playbook_candidate(tmp_path: Path):
    kb = StructuredKnowledgeBase(
        tmp_path / "kb",
        app_name="hotel-reservation",
        config=CrucibleConfig(prompt_version="v3", kb_scope="per_app"),
        renderer=PromptRenderer("v3"),
        driver=_DummyDriver(),
    )
    candidate = DiagnosisPlaybookDraft(
        slug="coredns-nxdomain",
        root_cause="CoreDNS template directives return NXDOMAIN for backend service names.",
        when_to_consider=["Gateway logs show repeated host-resolution failures to backend services."],
        disambiguators=["Backend pods are healthy but DNS lookups for their service names fail."],
        summary="Check whether cluster DNS is intentionally returning NXDOMAIN for service names.",
        triage_checks=["1. Inspect application logs for hostname resolution failures."],
        fault_localization_checks=["1. Map the failing request path to the backend hostname being resolved."],
        verification_checks=["1. Inspect the CoreDNS configuration for service-specific NXDOMAIN rules."],
        required_evidence=["CoreDNS config contains directives matching the failing service FQDNs."],
        known_confounders=["Backend Service object is actually missing."],
    )

    candidate_path = kb.write_diagnosis_playbook_candidate(timestamp="20260413_162613", draft=candidate)

    assert candidate_path.name == "diagnosis_playbook_candidate.json"
    assert json.loads(candidate_path.read_text())["slug"] == "coredns-nxdomain"


def test_structured_kb_writes_mitigation_records_and_candidate(tmp_path: Path):
    kb = StructuredKnowledgeBase(
        tmp_path / "kb",
        app_name="hotel-reservation",
        config=CrucibleConfig(prompt_version="v3", kb_scope="per_app"),
        renderer=PromptRenderer("v3"),
        driver=_DummyDriver(),
    )
    mitigation_run, recovery_run = kb.write_mitigation_records(
        timestamp="20260413_162613",
        mitigation_run_md="# Mitigation",
        recovery_mitigation_run_md="# Recovery Mitigation",
    )
    candidate = MitigationPlaybookDraft(
        slug="coredns-nxdomain",
        root_cause="CoreDNS template directives return NXDOMAIN for backend service names.",
        summary="Remove the targeted NXDOMAIN rules from CoreDNS and verify DNS recovery.",
        mitigation_procedure=["1. Patch the CoreDNS ConfigMap to remove the targeted template rules."],
        verification_checks=["1. Verify the affected service names resolve from an application pod."],
        rollback_stop_conditions=["Stop if the correct CoreDNS ConfigMap cannot be identified confidently."],
    )

    candidate_path = kb.write_mitigation_playbook_candidate(timestamp="20260413_162613", draft=candidate)

    assert mitigation_run.name == "mitigation_run.md"
    assert mitigation_run.read_text() == "# Mitigation"
    assert recovery_run is not None
    assert recovery_run.name == "recovery_mitigation_run.md"
    assert recovery_run.read_text() == "# Recovery Mitigation"
    assert candidate_path.name == "mitigation_playbook_candidate.json"
    assert json.loads(candidate_path.read_text())["slug"] == "coredns-nxdomain"


def test_diagnosis_run_record_does_not_duplicate_summary_or_include_recovery_content():
    record = DiagnosisRunRecord(
        problem_id="problem-a",
        app_name="Social Network",
        namespace="social-network",
        diagnosis_succeeded=False,
        agent_answer="Wrong DNS setting",
        agent_justification="The client points to an invalid resolver.",
        agent_causal_chain="bad resolver -> failed lookups",
        benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>",
        stage_outputs="## Triage Report\nObserved DNS failures.\n",
    )

    markdown = record.to_markdown()

    assert markdown.count("<benchmark_result>") == 1
    assert markdown.count("Answer: Wrong DNS setting") == 1
    assert "Recovery Diagnosis Investigation" not in markdown


def test_recovery_diagnosis_run_record_includes_recovery_stage_outputs_once():
    record = RecoveryDiagnosisRunRecord(
        problem_id="problem-a",
        app_name="Social Network",
        namespace="social-network",
        has_recovery_diagnosis=True,
        agent_answer="CoreDNS template returns NXDOMAIN",
        agent_justification="The Corefile contains an NXDOMAIN rule.",
        agent_causal_chain="NXDOMAIN rule -> lookup failure",
        benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>",
        stage_outputs="## Recovery Diagnosis Investigation\nChecked CoreDNS config.\n",
    )

    markdown = record.to_markdown()

    assert markdown.count("<benchmark_result>") == 1
    assert markdown.count("## Recovery Diagnosis Investigation") == 1


def test_mitigation_run_record_does_not_duplicate_summary_or_include_recovery_content():
    record = MitigationRunRecord(
        problem_id="problem-a",
        app_name="Social Network",
        namespace="social-network",
        mitigation_succeeded=True,
        agent_answer="Patched ConfigMap/coredns to remove the NXDOMAIN template rules.",
        agent_justification="Service-name lookups now resolve from the affected pod.",
        benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>",
        stage_outputs="## Mitigation Investigation\nPatched CoreDNS and verified DNS recovery.\n",
    )

    markdown = record.to_markdown()

    assert markdown.count("<benchmark_result>") == 1
    assert markdown.count("Mitigation: Patched ConfigMap/coredns") == 1
    assert "Recovery Mitigation Investigation" not in markdown


def test_recovery_mitigation_run_record_includes_recovery_stage_outputs_once():
    record = RecoveryMitigationRunRecord(
        problem_id="problem-a",
        app_name="Social Network",
        namespace="social-network",
        has_recovery_mitigation=True,
        agent_answer="Patched ConfigMap/coredns and rolled CoreDNS.",
        agent_justification="The target service names now resolve from the application pod.",
        benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>",
        stage_outputs="## Recovery Mitigation Investigation\nValidated the fix with DNS lookups.\n",
    )

    markdown = record.to_markdown()

    assert markdown.count("<benchmark_result>") == 1
    assert markdown.count("## Recovery Mitigation Investigation") == 1
