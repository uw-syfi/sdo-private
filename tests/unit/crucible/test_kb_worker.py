from __future__ import annotations

import json

import pytest

from sregym_agents.crucible.agents.base import AgentResult
from sregym_agents.crucible.knowledge_base.incident_review import DiagnosisPlaybookDraft, ReviewDecision
from sregym_agents.crucible.knowledge_base.root_cause import RootCauseStore


def _candidate(slug: str = "coredns-nxdomain") -> DiagnosisPlaybookDraft:
    return DiagnosisPlaybookDraft(
        slug=slug,
        root_cause="CoreDNS returns NXDOMAIN for backend service names.",
        when_to_consider=["Application logs show host resolution failures to internal services."],
        disambiguators=["Backend services exist but DNS lookups still fail."],
        summary="Inspect cluster DNS behavior for targeted NXDOMAIN responses.",
        triage_checks=["1. Review application logs for repeated service-name resolution failures."],
        verification_checks=["1. Inspect CoreDNS config for rules matching the failing service names."],
        required_evidence=["CoreDNS config contains a rule returning NXDOMAIN for the failing service FQDN."],
        known_confounders=["The Service object is missing entirely."],
    )


def test_review_decision_rejects_merge_without_targets():
    with pytest.raises(ValueError, match="target slug"):
        ReviewDecision(
            primary_failure_mode="retrieval_failure",
            relevant_existing_playbooks=["dns-failure"],
            recommended_action="merge_playbooks",
            target_slugs=[],
            reasoning="A relevant playbook existed and should be merged.",
        )


def test_review_decision_rejects_validation_failure_without_existing_playbooks():
    with pytest.raises(ValueError, match="requires at least one relevant existing playbook"):
        ReviewDecision(
            primary_failure_mode="playbook_validation_failure",
            relevant_existing_playbooks=[],
            recommended_action="reject_playbook",
            rejection_reason="The candidate is too incident-specific.",
            reasoning="The schema should reject this inconsistent combination.",
        )


def test_review_decision_rejects_add_with_targets():
    with pytest.raises(ValueError, match="cannot specify target_slugs"):
        ReviewDecision(
            primary_failure_mode="missing_playbook",
            relevant_existing_playbooks=[],
            recommended_action="add_playbook",
            target_slugs=["dns-failure"],
            reasoning="An add action cannot target an existing slug.",
        )


@pytest.mark.asyncio
async def test_process_task_adds_candidate_playbook(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    diagnosis_path = tmp_path / "diagnosis_run.md"
    recovery_path = tmp_path / "recovery_diagnosis_run.md"
    candidate_path = tmp_path / "candidate.json"
    diagnosis_path.write_text("# Diagnosis")
    recovery_path.write_text("# Recovery")
    candidate_path.write_text(json.dumps(_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": str(diagnosis_path),
                "recovery_diagnosis_run_file": str(recovery_path),
                "diagnosis_playbook_candidate_file": str(candidate_path),
                "stage_outputs_file": None,
                "kb_dir": str(kb_dir),
                "kb_type": "structured",
                "model_id": "test-model",
                "app_name": "social-network",
                "include_benchmark_results": True,
                "kb_scope": "per_app",
                "kb_runtime_mode": "playbook-first",
                "kb_update_mode": "async-review",
                "problem_id": "problem-1",
                "prompt_version": "v3",
                "diagnosis_succeeded": False,
                "mitigation_succeeded": False,
                "timestamp": "20260414_000000",
            }
        )
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            return AgentResult(
                output=ReviewDecision(
                    primary_failure_mode="missing_playbook",
                    relevant_existing_playbooks=[],
                    recommended_action="add_playbook",
                    reasoning="The candidate captures a novel diagnosis pattern.",
                )
            )

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    await kb_worker.process_task(task_path)

    saved = store.load_diagnosis("coredns-nxdomain")
    assert saved is not None
    assert saved.front_matter.root_cause == "CoreDNS returns NXDOMAIN for backend service names."


@pytest.mark.asyncio
async def test_process_task_merges_candidate_into_existing_playbook(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker
    from sregym_agents.crucible.knowledge_base.root_cause import DiagnosisFrontMatter, DiagnosisPlaybook

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    diagnosis_path = tmp_path / "diagnosis_run.md"
    recovery_path = tmp_path / "recovery_diagnosis_run.md"
    candidate_path = tmp_path / "candidate.json"
    diagnosis_path.write_text("# Diagnosis")
    recovery_path.write_text("# Recovery")
    candidate_path.write_text(json.dumps(_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": str(diagnosis_path),
                "recovery_diagnosis_run_file": str(recovery_path),
                "diagnosis_playbook_candidate_file": str(candidate_path),
                "stage_outputs_file": None,
                "kb_dir": str(kb_dir),
                "kb_type": "structured",
                "model_id": "test-model",
                "app_name": "social-network",
                "include_benchmark_results": True,
                "kb_scope": "per_app",
                "kb_runtime_mode": "playbook-first",
                "kb_update_mode": "async-review",
                "problem_id": "problem-1",
                "prompt_version": "v3",
                "diagnosis_succeeded": False,
                "mitigation_succeeded": False,
                "timestamp": "20260414_000000",
            }
        )
    )

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    store.save_diagnosis(
        DiagnosisPlaybook(
            front_matter=DiagnosisFrontMatter(
                slug="dns-failure",
                root_cause="DNS is broken for service lookups.",
                when_to_consider=["Pods cannot resolve service hostnames."],
                disambiguators=["Service objects exist."],
            ),
            summary="Check cluster DNS for application service resolution failures.",
            triage_checks=["1. Confirm the app is failing on DNS resolution."],
            verification_checks=["1. Run DNS lookups for the failing service names."],
            required_evidence=["DNS lookups fail for existing service names."],
            known_confounders=["Backend pods are crashing."],
        ),
        created_from="seed",
    )
    store.save_diagnosis(
        DiagnosisPlaybook(
            front_matter=DiagnosisFrontMatter(
                slug="dns-targeted-nxdomain",
                root_cause="CoreDNS injects NXDOMAIN for selected service names.",
                when_to_consider=["Only specific service names fail to resolve."],
                disambiguators=["CoreDNS config contains name-specific overrides."],
            ),
            summary="Check for targeted NXDOMAIN rules in cluster DNS.",
            triage_checks=["1. Compare healthy and failing DNS lookups."],
            verification_checks=["1. Inspect CoreDNS config for the failing names."],
            required_evidence=["CoreDNS config mentions the failing service names."],
            known_confounders=["The failing service names are misspelled."],
        ),
        created_from="seed",
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id
            self.calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentResult(
                    output=ReviewDecision(
                        primary_failure_mode="playbook_validation_failure",
                        relevant_existing_playbooks=["dns-failure", "dns-targeted-nxdomain"],
                        recommended_action="merge_playbooks",
                        target_slugs=["dns-failure", "dns-targeted-nxdomain"],
                        reasoning="The candidate should become the merged canonical DNS diagnosis playbook.",
                    )
                )
            return AgentResult(output=_candidate(slug="merged-ignored"))

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    await kb_worker.process_task(task_path)

    merged = store.load_diagnosis("dns-failure")
    deprecated_meta = store.load_meta("dns-targeted-nxdomain")
    assert merged is not None
    assert merged.front_matter.root_cause == "CoreDNS returns NXDOMAIN for backend service names."
    assert deprecated_meta.status == "deprecated"


@pytest.mark.asyncio
async def test_process_task_accepts_missing_optional_recovery_run(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    diagnosis_path = tmp_path / "diagnosis_run.md"
    candidate_path = tmp_path / "candidate.json"
    diagnosis_path.write_text("# Diagnosis")
    candidate_path.write_text(json.dumps(_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": str(diagnosis_path),
                "recovery_diagnosis_run_file": None,
                "diagnosis_playbook_candidate_file": str(candidate_path),
                "stage_outputs_file": None,
                "kb_dir": str(kb_dir),
                "kb_type": "structured",
                "model_id": "test-model",
                "app_name": "social-network",
                "include_benchmark_results": True,
                "kb_scope": "per_app",
                "kb_runtime_mode": "playbook-first",
                "kb_update_mode": "async-review",
                "problem_id": "problem-1",
                "prompt_version": "v3",
                "diagnosis_succeeded": False,
                "mitigation_succeeded": False,
                "timestamp": "20260414_000000",
            }
        )
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            return AgentResult(
                output=ReviewDecision(
                    primary_failure_mode="missing_playbook",
                    relevant_existing_playbooks=[],
                    recommended_action="add_playbook",
                    reasoning="The diagnosis run and candidate are sufficient without recovery context.",
                )
            )

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    await kb_worker.process_task(task_path)

    saved = store.load_diagnosis("coredns-nxdomain")
    assert saved is not None
