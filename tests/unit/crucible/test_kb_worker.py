from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from sregym_agents.crucible.agents.base import AgentResult
from sregym_agents.crucible.knowledge_base.incident_review import (
    DiagnosisPlaybookDraft,
    MitigationPlaybookDraft,
    PlaceholderResolutionRule,
    ReviewDecision,
    TriageAreaCandidate,
)
from sregym_agents.crucible.knowledge_base.root_cause import RootCauseStore


def _candidate(slug: str = "coredns-nxdomain") -> DiagnosisPlaybookDraft:
    return DiagnosisPlaybookDraft(
        slug=slug,
        root_cause="CoreDNS returns NXDOMAIN for backend service names.",
        when_to_consider=["Application logs show host resolution failures to internal services."],
        disambiguators=["Backend services exist but DNS lookups still fail."],
        summary="Inspect cluster DNS behavior for targeted NXDOMAIN responses.",
        triage_checks=["1. Review application logs for repeated service-name resolution failures."],
        fault_localization_checks=["1. Trace the failing request path to the backend hostname being resolved."],
        verification_checks=["1. Inspect CoreDNS config for rules matching the failing service names."],
        required_evidence=["CoreDNS config contains a rule returning NXDOMAIN for the failing service FQDN."],
        known_confounders=["The Service object is missing entirely."],
    )


def _mitigation_candidate(slug: str = "coredns-nxdomain") -> MitigationPlaybookDraft:
    return MitigationPlaybookDraft(
        slug=slug,
        root_cause="CoreDNS returns NXDOMAIN for backend service names.",
        summary="Remove the targeted CoreDNS rule and verify service-name resolution recovers.",
        mitigation_procedure=["1. Patch the CoreDNS ConfigMap to remove the targeted NXDOMAIN rule."],
        placeholder_resolution=[
            PlaceholderResolutionRule(
                symbol="<AFFECTED_SERVICE_FQDNS>",
                resolution_guidance=(
                    "Resolve from the diagnosis-confirmed service names. This may be one FQDN or a set "
                    "of service names covered by the same CoreDNS override."
                ),
            )
        ],
        verification_checks=["1. Verify the affected service names resolve from the application pod."],
        rollback_stop_conditions=["Stop if the correct CoreDNS ConfigMap cannot be identified confidently."],
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


def test_review_decision_rejects_retrieval_failure_with_add_playbook():
    with pytest.raises(ValueError, match="incoherent with add_playbook"):
        ReviewDecision(
            primary_failure_mode="retrieval_failure",
            relevant_existing_playbooks=["some-adjacent-playbook"],
            recommended_action="add_playbook",
            reasoning=(
                "Classifier lists an adjacent playbook but wants to add a new one — "
                "this should be missing_playbook + add_playbook instead."
            ),
        )


def test_review_decision_rejects_playbook_validation_failure_with_add_playbook():
    with pytest.raises(ValueError, match="incoherent with add_playbook"):
        ReviewDecision(
            primary_failure_mode="playbook_validation_failure",
            relevant_existing_playbooks=["some-adjacent-playbook"],
            recommended_action="add_playbook",
            reasoning="Same incoherence as retrieval_failure + add.",
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
            fault_localization_checks=["1. Identify which upstream hostname the failing request path depends on."],
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
            fault_localization_checks=["1. Map the failing symptom to the dependent service FQDN."],
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


@pytest.mark.asyncio
async def test_process_task_reviews_success_authored_diagnosis_candidate_with_success_prompt(tmp_path, monkeypatch):
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
                "diagnosis_playbook_candidate_origin": "success",
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
                "diagnosis_succeeded": True,
                "mitigation_succeeded": False,
                "timestamp": "20260414_000000",
            }
        )
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            assert "successful diagnosis workflow" in kwargs["prompt"].lower()
            assert "recovery-produced diagnosis playbook candidate" not in kwargs["prompt"].lower()
            return AgentResult(
                output=ReviewDecision(
                    primary_failure_mode="missing_playbook",
                    relevant_existing_playbooks=[],
                    recommended_action="add_playbook",
                    reasoning="The successful run demonstrates a novel reusable diagnosis pattern.",
                )
            )

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    await kb_worker.process_task(task_path)

    saved = store.load_diagnosis("coredns-nxdomain")
    assert saved is not None


@pytest.mark.asyncio
async def test_process_task_refines_triage_priors_from_candidate(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker
    from sregym_agents.crucible.tools import TriageArea, TriagePriors

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    diagnosis_path = tmp_path / "diagnosis_run.md"
    recovery_path = tmp_path / "recovery_diagnosis_run.md"
    triage_candidate_path = tmp_path / "triage_candidate.json"
    diagnosis_path.write_text("# Diagnosis")
    recovery_path.write_text("# Recovery")
    triage_candidate_path.write_text(
        json.dumps(
            TriageAreaCandidate(
                area_name="DNS and Service Discovery",
                hints=[
                    (
                        "Inspect entrypoint logs for hostname-resolution failures when smoke tests only "
                        "show generic HTTP errors."
                    ),
                    "Test the failing service FQDN and one known-good service name from the same pod.",
                ],
                grounding=[
                    "Initial triage only surfaced generic HTTP failures.",
                    "Recovery found a targeted DNS failure.",
                ],
            ).model_dump(mode="python")
        )
    )

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": str(diagnosis_path),
                "recovery_diagnosis_run_file": str(recovery_path),
                "diagnosis_playbook_candidate_file": None,
                "triage_area_candidate_file": str(triage_candidate_path),
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
                output=TriagePriors(
                    areas=[
                        TriageArea(
                            name="DNS and Service Discovery",
                            hints=[
                                (
                                    "Inspect entrypoint logs for hostname-resolution failures when smoke tests "
                                    "only show generic HTTP errors."
                                ),
                                "Test the failing service FQDN and one known-good service name from the same pod.",
                            ],
                        )
                    ]
                )
            )

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    await kb_worker.process_task(task_path)

    triage_priors_path = kb_dir / "v3" / "apps" / "social-network" / "triage_priors.yaml"
    assert triage_priors_path.exists()
    saved = yaml.safe_load(triage_priors_path.read_text())
    assert saved == {
        "areas": [
            {
                "name": "DNS and Service Discovery",
                "hints": [
                    (
                        "Inspect entrypoint logs for hostname-resolution failures when smoke tests only show "
                        "generic HTTP errors."
                    ),
                    "Test the failing service FQDN and one known-good service name from the same pod.",
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_process_task_saves_mitigation_candidate_for_existing_diagnosis(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    mitigation_path = tmp_path / "mitigation_run.md"
    mitigation_candidate_path = tmp_path / "mitigation_candidate.json"
    mitigation_path.write_text("# Mitigation")
    mitigation_candidate_path.write_text(json.dumps(_mitigation_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": None,
                "recovery_diagnosis_run_file": None,
                "diagnosis_playbook_candidate_file": None,
                "mitigation_run_file": str(mitigation_path),
                "recovery_mitigation_run_file": None,
                "mitigation_playbook_candidate_file": str(mitigation_candidate_path),
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
                "diagnosis_succeeded": True,
                "mitigation_succeeded": True,
                "timestamp": "20260414_000000",
            }
        )
    )

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    store.save_diagnosis(kb_worker._draft_to_playbook(_candidate()), created_from="seed")

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            return AgentResult(output=SimpleNamespace())

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    await kb_worker.process_task(task_path)

    saved = store.load_mitigation("coredns-nxdomain")
    assert saved is not None
    assert saved.summary == "Remove the targeted CoreDNS rule and verify service-name resolution recovers."


@pytest.mark.asyncio
async def test_process_task_merges_mitigation_candidate_into_existing_playbook(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker
    from sregym_agents.crucible.knowledge_base.root_cause import MitigationFrontMatter, MitigationPlaybook

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    mitigation_path = tmp_path / "mitigation_run.md"
    recovery_path = tmp_path / "recovery_mitigation_run.md"
    mitigation_candidate_path = tmp_path / "mitigation_candidate.json"
    mitigation_path.write_text("# Mitigation")
    recovery_path.write_text("# Recovery Mitigation")
    mitigation_candidate_path.write_text(json.dumps(_mitigation_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": None,
                "recovery_diagnosis_run_file": None,
                "diagnosis_playbook_candidate_file": None,
                "mitigation_run_file": str(mitigation_path),
                "recovery_mitigation_run_file": str(recovery_path),
                "mitigation_playbook_candidate_file": str(mitigation_candidate_path),
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
                "diagnosis_succeeded": True,
                "mitigation_succeeded": True,
                "timestamp": "20260414_000000",
            }
        )
    )

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    store.save_diagnosis(kb_worker._draft_to_playbook(_candidate()), created_from="seed")
    store.save_mitigation(
        MitigationPlaybook(
            front_matter=MitigationFrontMatter(
                slug="coredns-nxdomain",
                root_cause="CoreDNS returns NXDOMAIN for backend service names.",
            ),
            summary="Restart the application pods and hope DNS recovers.",
            mitigation_procedure=["1. Restart the affected pods."],
            verification_checks=["1. Confirm the application error rate drops."],
            rollback_stop_conditions=["Stop if the restart does not improve symptoms."],
        ),
        created_from="seed",
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            assert kwargs["agent_name"] == "kb-merge-mitigation-playbooks"
            return AgentResult(output=_mitigation_candidate())

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    await kb_worker.process_task(task_path)

    merged = store.load_mitigation("coredns-nxdomain")
    assert merged is not None
    assert merged.summary == "Remove the targeted CoreDNS rule and verify service-name resolution recovers."


@pytest.mark.asyncio
async def test_process_task_skips_mitigation_candidate_without_matching_diagnosis(tmp_path, monkeypatch):
    from sregym_agents.crucible import kb_worker

    kb_dir = tmp_path / "kb"
    reviews_pending = kb_dir / "v3" / "reviews" / "pending"
    reviews_pending.mkdir(parents=True)
    mitigation_path = tmp_path / "mitigation_run.md"
    mitigation_candidate_path = tmp_path / "mitigation_candidate.json"
    mitigation_path.write_text("# Mitigation")
    mitigation_candidate_path.write_text(json.dumps(_mitigation_candidate().model_dump(mode="python")))

    task_path = reviews_pending / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "diagnosis_run_file": None,
                "recovery_diagnosis_run_file": None,
                "diagnosis_playbook_candidate_file": None,
                "mitigation_run_file": str(mitigation_path),
                "recovery_mitigation_run_file": None,
                "mitigation_playbook_candidate_file": str(mitigation_candidate_path),
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
                "diagnosis_succeeded": True,
                "mitigation_succeeded": True,
                "timestamp": "20260414_000000",
            }
        )
    )

    class FakeDriver:
        def __init__(self, model_id: str):
            self.model_id = model_id

        async def run(self, **kwargs):
            return AgentResult(output=SimpleNamespace())

    monkeypatch.setattr("sregym_agents.crucible.agents.PydanticAIDriver", FakeDriver)

    store = RootCauseStore(kb_dir / "v3" / "apps" / "social-network")
    await kb_worker.process_task(task_path)

    assert store.load_mitigation("coredns-nxdomain") is None


def test_read_playbook_tool_returns_diagnosis_markdown(tmp_path):
    from sregym_agents.crucible.kb_worker import _make_read_playbook_tool
    from sregym_agents.crucible.knowledge_base.root_cause import DiagnosisFrontMatter, DiagnosisPlaybook

    store = RootCauseStore(tmp_path / "kb")
    store.save_diagnosis(
        DiagnosisPlaybook(
            front_matter=DiagnosisFrontMatter(
                slug="my-pb",
                root_cause="Root cause text",
                when_to_consider=["signal A"],
                disambiguators=["disambig 1"],
            ),
            summary="Summary text",
            triage_checks=["1. First triage check"],
            fault_localization_checks=["1. Localize"],
            verification_checks=["1. Verify"],
            required_evidence=["evidence 1"],
            known_confounders=["confounder 1"],
        ),
        created_from="test",
    )

    tool = _make_read_playbook_tool(store)
    markdown = tool(ctx=None, playbook_type="diagnosis", slug="my-pb")
    assert "my-pb" in markdown
    assert "Summary text" in markdown
    assert "First triage check" in markdown
    assert "Verify" in markdown


def test_read_playbook_tool_returns_mitigation_markdown(tmp_path):
    from sregym_agents.crucible.kb_worker import _make_read_playbook_tool
    from sregym_agents.crucible.knowledge_base.root_cause import (
        DiagnosisFrontMatter,
        DiagnosisPlaybook,
        MitigationFrontMatter,
        MitigationPlaybook,
    )

    store = RootCauseStore(tmp_path / "kb")
    store.save_diagnosis(
        DiagnosisPlaybook(
            front_matter=DiagnosisFrontMatter(
                slug="pb-slug",
                root_cause="rc",
                when_to_consider=["x"],
                disambiguators=["y"],
            ),
            summary="s",
            triage_checks=["1. t"],
            fault_localization_checks=["1. fl"],
            verification_checks=["1. v"],
            required_evidence=["e"],
        ),
        created_from="test",
    )
    store.save_mitigation(
        MitigationPlaybook(
            front_matter=MitigationFrontMatter(slug="pb-slug", root_cause="rc"),
            summary="mitigation summary",
            mitigation_procedure=["1. Apply a patch"],
            verification_checks=["1. Confirm recovery"],
        ),
        created_from="test",
    )

    tool = _make_read_playbook_tool(store)
    markdown = tool(ctx=None, playbook_type="mitigation", slug="pb-slug")
    assert "mitigation summary" in markdown
    assert "Apply a patch" in markdown


def test_read_playbook_tool_raises_on_missing_slug(tmp_path):
    from sregym_agents.crucible.kb_worker import _make_read_playbook_tool

    store = RootCauseStore(tmp_path / "kb")

    tool = _make_read_playbook_tool(store)
    with pytest.raises(ValueError, match="not found"):
        tool(ctx=None, playbook_type="diagnosis", slug="nope")


def test_read_playbook_tool_raises_on_invalid_type(tmp_path):
    from sregym_agents.crucible.kb_worker import _make_read_playbook_tool

    store = RootCauseStore(tmp_path / "kb")

    tool = _make_read_playbook_tool(store)
    with pytest.raises(ValueError, match="Invalid playbook_type"):
        tool(ctx=None, playbook_type="bogus", slug="whatever")
