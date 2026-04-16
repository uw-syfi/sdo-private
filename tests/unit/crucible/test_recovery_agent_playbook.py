from __future__ import annotations

from typing import Any

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.agents.base import AgentDriver, AgentResult
from sregym_agents.crucible.agents.recovery_agent import RecoveryAgent, RecoveryRunResult
from sregym_agents.crucible.knowledge_base.incident_review import (
    DiagnosisPlaybookDraft,
    MitigationPlaybookDraft,
    PlaceholderResolutionRule,
    TriageAreaCandidate,
)
from sregym_agents.crucible.tools import SharedFile, SRESubmission


class _FakeDriver(AgentDriver):
    def __init__(self, output: Any):
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        *,
        prompt: str,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        output_type: type[Any] = str,
        agent_name: str = "",
        timeout: int | None = None,
        model_settings: dict[str, Any] | None = None,
        message_history: list[Any] | None = None,
        usage_collector: Any | None = None,
        **kwargs: Any,
    ) -> AgentResult[Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "tools": tools,
                "output_type": output_type,
                "agent_name": agent_name,
                "timeout": timeout,
                "model_settings": model_settings,
                "message_history": message_history,
                "usage_collector": usage_collector,
                **kwargs,
            }
        )
        return AgentResult(output=self.output)


@pytest.mark.asyncio
async def test_recovery_agent_builds_diagnosis_playbook_candidate():
    driver = _FakeDriver(
        DiagnosisPlaybookDraft(
            slug="coredns-nxdomain",
            root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
            when_to_consider=["Application logs show service-hostname resolution failures."],
            disambiguators=["Backend services exist but lookups still return NXDOMAIN."],
            summary="Check whether cluster DNS is intentionally returning NXDOMAIN for service names.",
            triage_checks=["1. Inspect application logs for host-resolution errors."],
            fault_localization_checks=["1. Trace the failing request path to the dependent backend hostname."],
            verification_checks=["1. Inspect CoreDNS configuration for matching NXDOMAIN rules."],
            required_evidence=["CoreDNS config contains a rule matching the failing service FQDN."],
            known_confounders=["The Service object is missing."],
        )
    )
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_diagnosis_playbook_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        original_answer="Pod networking is broken.",
        grounded_answer="ConfigMap/coredns returns NXDOMAIN for post-storage-service.social-network.svc.cluster.local.",
        grounded_justification="kubectl showed CoreDNS template rules for the failing service FQDN.",
        grounded_causal_chain="CoreDNS rule -> NXDOMAIN -> app cannot resolve backend service names.",
        recovery_message_history=[{"role": "assistant", "content": "grounded diagnosis context"}],
    )

    assert candidate is not None
    assert candidate.slug == "coredns-nxdomain"
    assert driver.calls[0]["agent_name"] == "recovery-diagnosis-playbook"
    assert driver.calls[0]["message_history"] == [{"role": "assistant", "content": "grounded diagnosis context"}]


@pytest.mark.asyncio
async def test_recovery_agent_skips_playbook_candidate_without_grounded_diagnosis():
    driver = _FakeDriver(None)
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_diagnosis_playbook_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        original_answer="Pod networking is broken.",
        grounded_answer="",
        grounded_justification="",
    )

    assert candidate is None
    assert driver.calls == []


@pytest.mark.asyncio
async def test_recovery_agent_builds_triage_area_candidate():
    driver = _FakeDriver(
        TriageAreaCandidate(
            area_name="DNS and Service Discovery",
            hints=[
                (
                    "Inspect entrypoint logs for hostname-resolution failures before assuming generic HTTP "
                    "errors are application-only."
                ),
                "Test the failing service FQDN and one known-good service name from the same pod.",
            ],
            grounding=[
                "Initial triage only surfaced generic HTTP failures.",
                "Later investigation found repeated host-resolution errors in the entrypoint component.",
            ],
        )
    )
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_triage_area_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        original_answer="Pod networking is broken.",
        original_justification="Requests were failing.",
        grounded_answer="CoreDNS returned NXDOMAIN for a specific service.",
        grounded_justification="CoreDNS config and DNS behavior matched the targeted failure.",
        stage_outputs="## Triage Report\n- generic HTTP failures only\n",
        recovery_message_history=[{"role": "assistant", "content": "grounded diagnosis context"}],
    )

    assert candidate is not None
    assert candidate.area_name == "DNS and Service Discovery"
    assert driver.calls[0]["agent_name"] == "recovery-triage-area-candidate"
    assert driver.calls[0]["message_history"] == [{"role": "assistant", "content": "grounded diagnosis context"}]


@pytest.mark.asyncio
async def test_recovery_agent_skips_triage_area_candidate_without_stage_outputs():
    driver = _FakeDriver(None)
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_triage_area_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        original_answer="Pod networking is broken.",
        grounded_answer="CoreDNS returned NXDOMAIN for a specific service.",
        grounded_justification="CoreDNS config matched the failure.",
        stage_outputs="",
        recovery_message_history=[{"role": "assistant", "content": "grounded diagnosis context"}],
    )

    assert candidate is None
    assert driver.calls == []


@pytest.mark.asyncio
async def test_recovery_agent_builds_mitigation_playbook_candidate():
    driver = _FakeDriver(
        MitigationPlaybookDraft(
            slug="wrong-slug",
            root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
            summary="Remove the CoreDNS override and verify service-name resolution recovers.",
            mitigation_procedure=["1. Patch the CoreDNS ConfigMap to remove the targeted NXDOMAIN template rule."],
            placeholder_resolution=[
                PlaceholderResolutionRule(
                    symbol="<AFFECTED_SERVICE_FQDNS>",
                    resolution_guidance=(
                        "Resolve from the diagnosis-confirmed service names. This may be one FQDN or a set "
                        "of service names matched by the same CoreDNS rule."
                    ),
                )
            ],
            verification_checks=["1. Verify the affected service names resolve from an application pod."],
            rollback_stop_conditions=["Stop if the CoreDNS ConfigMap cannot be updated confidently."],
        )
    )
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_mitigation_playbook_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        root_cause_slug="coredns-nxdomain",
        root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
        diagnosis_answer="CoreDNS injects NXDOMAIN for post-storage-service.",
        original_answer="Restarted the application pods.",
        original_justification="Pods were healthy after restart.",
        grounded_answer="Patched ConfigMap/coredns to remove the NXDOMAIN template blocks.",
        grounded_justification="DNS lookups for the affected service names now resolve successfully.",
        recovery_message_history=[{"role": "assistant", "content": "grounded mitigation context"}],
    )

    assert candidate is not None
    assert candidate.slug == "coredns-nxdomain"
    assert candidate.placeholder_resolution[0].symbol == "<AFFECTED_SERVICE_FQDNS>"
    assert driver.calls[0]["agent_name"] == "recovery-mitigation-playbook"
    assert driver.calls[0]["message_history"] == [{"role": "assistant", "content": "grounded mitigation context"}]


@pytest.mark.asyncio
async def test_recovery_agent_skips_mitigation_playbook_candidate_without_grounded_context():
    driver = _FakeDriver(None)
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))

    candidate = await agent.build_mitigation_playbook_candidate(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        root_cause_slug="coredns-nxdomain",
        root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
        diagnosis_answer="CoreDNS injects NXDOMAIN for post-storage-service.",
        original_answer="Restarted the application pods.",
        grounded_answer="",
        grounded_justification="",
    )

    assert candidate is None
    assert driver.calls == []


@pytest.mark.asyncio
async def test_recovery_agent_returns_wrapper_with_submission_and_history(tmp_path):
    driver = _FakeDriver(SRESubmission(answer="root cause", justification="evidence", causal_chain="A -> B -> C"))
    agent = RecoveryAgent(driver=driver, model_id="test-model", renderer=PromptRenderer("v3"))
    shared_path = tmp_path / "shared.md"
    shared_path.write_text("")

    result = await agent.run_diagnosis(
        app_info={"app_name": "social-network", "namespace": "social-network"},
        shared_file=SharedFile(shared_path),
        original_answer="wrong answer",
        benchmark_block=(
            '<benchmark_result><oracle>{"Diagnosis":{"reasoning":"actual root cause"}}</oracle></benchmark_result>'
        ),
    )

    assert isinstance(result, RecoveryRunResult)
    assert result is not None
    assert result.submission.answer == "root cause"
    assert result.submission.justification == "evidence"
    assert result.message_history == []
