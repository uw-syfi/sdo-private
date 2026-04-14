from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.agents.sre_agent import SREAgent
from sregym_agents.crucible.knowledge_base.root_cause import (
    DiagnosisFrontMatter,
    DiagnosisPlaybook,
    MitigationFrontMatter,
    MitigationPlaybook,
    RootCauseStore,
)
from sregym_agents.crucible.tools import LTMShortCircuit, SharedFile, SREDeps
from sregym_agents.crucible.tools._kb_tools import (
    MitigationApplication,
    MitigationPlaybookMatch,
    search_prior_mitigations_impl,
)


def _diagnosis_playbook(slug: str = "coredns-nxdomain") -> DiagnosisPlaybook:
    return DiagnosisPlaybook(
        front_matter=DiagnosisFrontMatter(
            slug=slug,
            root_cause="CoreDNS returns NXDOMAIN for a valid service hostname.",
            when_to_consider=["Pods fail when resolving a specific in-cluster Service hostname."],
            disambiguators=["The Service object exists and other DNS lookups still work."],
        ),
        summary="Check whether CoreDNS is intentionally returning NXDOMAIN for the affected Service FQDN.",
        triage_checks=["1. Inspect application logs for DNS resolution errors."],
        fault_localization_checks=["1. Inspect the CoreDNS ConfigMap for service-specific rules."],
        verification_checks=["1. Confirm CoreDNS returns NXDOMAIN for the affected Service FQDN."],
        required_evidence=["CoreDNS config explicitly matches the affected Service hostname."],
        known_confounders=["The Service itself is missing."],
    )


def _mitigation_playbook(slug: str = "coredns-nxdomain") -> MitigationPlaybook:
    return MitigationPlaybook(
        front_matter=MitigationFrontMatter(
            slug=slug,
            root_cause="CoreDNS returns NXDOMAIN for a valid service hostname.",
        ),
        summary="Remove the targeted NXDOMAIN rule from CoreDNS.",
        mitigation_procedure=["1. Patch the CoreDNS ConfigMap to remove the targeted template rule."],
        verification_checks=["1. Confirm the affected Service hostname resolves successfully."],
        rollback_stop_conditions=["Stop if the CoreDNS ConfigMap cannot be identified confidently."],
    )


def _make_deps(tmp_path, *, with_mitigation: bool = True) -> tuple[SREDeps, AsyncMock]:
    kb_dir = tmp_path / "kb"
    store = RootCauseStore(kb_dir)
    store.save_diagnosis(_diagnosis_playbook(), created_from="problem-a")
    if with_mitigation:
        store.save_mitigation(_mitigation_playbook(), created_from="problem-a")

    shared_path = tmp_path / "shared.md"
    shared_path.write_text("# session\n")
    run_subagent = AsyncMock()
    deps = SREDeps(
        namespace="social-network",
        shared_file=SharedFile(shared_path),
        iteration=1,
        stage="mitigation",
        model_id="test-model",
        renderer=PromptRenderer("v3"),
        kb_view_dir=kb_dir,
        run_subagent=run_subagent,
    )
    return deps, run_subagent


@pytest.mark.asyncio
async def test_search_prior_mitigations_short_circuits_on_success(tmp_path) -> None:
    deps, run_subagent = _make_deps(tmp_path)
    run_subagent.return_value = MitigationPlaybookMatch(
        slug="coredns-nxdomain",
        root_cause="CoreDNS returns NXDOMAIN for a valid service hostname.",
        reasoning="The recovered diagnosis exactly matches the stored root cause class.",
        confident=True,
    )

    with patch(
        "sregym_agents.crucible.tools._kb_tools.run_single_mitigation_playbook",
        new=AsyncMock(
            return_value=MitigationApplication(
                strategy_index=0,
                root_cause_class="CoreDNS returns NXDOMAIN for a valid service hostname.",
                applied=True,
                applied_steps=["Removed the template rule from the CoreDNS ConfigMap."],
                verification_evidence=["nslookup post-storage-service.social-network.svc.cluster.local now resolves."],
                mitigation_summary="Removed the CoreDNS NXDOMAIN rule for post-storage-service.",
                reasoning="All mitigation verification checks passed.",
            )
        ),
    ):
        with pytest.raises(LTMShortCircuit) as exc_info:
            await search_prior_mitigations_impl(
                deps,
                "CoreDNS returns NXDOMAIN for post-storage-service.social-network.svc.cluster.local.",
            )

    assert exc_info.value.confirmed == ["Removed the CoreDNS NXDOMAIN rule for post-storage-service."]
    assert exc_info.value.iteration == 1
    shared = deps.shared_file.read()
    assert "KB Mitigation Retrieval" in shared
    assert "Playbook Shortcut (Mitigation)" in shared


@pytest.mark.asyncio
async def test_search_prior_mitigations_returns_no_match(tmp_path) -> None:
    deps, run_subagent = _make_deps(tmp_path)
    run_subagent.return_value = MitigationPlaybookMatch(
        slug=None,
        root_cause="",
        reasoning="No diagnosis playbook matches this root cause confidently.",
        confident=False,
    )

    result = await search_prior_mitigations_impl(deps, "Some completely novel root cause.")
    payload = json.loads(result)

    assert payload["matched_slug"] is None
    assert payload["playbook_attempted"] is False
    assert payload["playbook_applied"] is False
    assert payload["reasoning"] == "No diagnosis playbook matches this root cause confidently."


@pytest.mark.asyncio
async def test_search_prior_mitigations_returns_when_mitigation_playbook_missing(tmp_path) -> None:
    deps, run_subagent = _make_deps(tmp_path, with_mitigation=False)
    run_subagent.return_value = MitigationPlaybookMatch(
        slug="coredns-nxdomain",
        root_cause="CoreDNS returns NXDOMAIN for a valid service hostname.",
        reasoning="The recovered diagnosis matches the stored root cause class.",
        confident=True,
    )

    result = await search_prior_mitigations_impl(
        deps,
        "CoreDNS returns NXDOMAIN for post-storage-service.social-network.svc.cluster.local.",
    )
    payload = json.loads(result)

    assert payload["matched_slug"] == "coredns-nxdomain"
    assert payload["playbook_found"] is False
    assert payload["playbook_attempted"] is False
    assert payload["playbook_applied"] is False


def test_sre_agent_mitigation_tools_include_search_prior_mitigations() -> None:
    agent = SREAgent(
        driver=AsyncMock(spec=Any),
        model_id="test-model",
        renderer=PromptRenderer("v3"),
        config=None,
    )

    tool_names = {tool.__name__ for tool in agent._assemble_tools("mitigation")}

    assert "search_prior_mitigations" in tool_names
