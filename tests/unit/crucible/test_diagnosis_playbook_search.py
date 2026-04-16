from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.knowledge_base.root_cause import (
    DiagnosisFrontMatter,
    DiagnosisPlaybook,
    RootCauseStore,
)
from sregym_agents.crucible.tools import SharedFile, SREDeps
from sregym_agents.crucible.tools._bash_tools import exec_bash_any, grep, read_file
from sregym_agents.crucible.tools._kb_tools import DifferentialDiagnosis, search_prior_incidents_impl


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


def _make_deps(tmp_path) -> tuple[SREDeps, AsyncMock]:
    kb_dir = tmp_path / "kb"
    store = RootCauseStore(kb_dir)
    store.save_diagnosis(_diagnosis_playbook(), created_from="problem-a")

    shared_path = tmp_path / "shared.md"
    shared_path.write_text("# session\n")
    run_subagent = AsyncMock()
    deps = SREDeps(
        namespace="social-network",
        shared_file=SharedFile(shared_path),
        iteration=1,
        stage="diagnosis",
        model_id="test-model",
        renderer=PromptRenderer("v3"),
        kb_view_dir=kb_dir,
        run_subagent=run_subagent,
    )
    return deps, run_subagent


@pytest.mark.asyncio
async def test_search_prior_incidents_gives_ltm_search_read_only_tools(tmp_path) -> None:
    deps, run_subagent = _make_deps(tmp_path)
    run_subagent.return_value = DifferentialDiagnosis(
        candidate_root_causes=[],
        novel_cause_signals="Check exact DNS errors.",
        caveats="Current symptoms are generic.",
    )

    await search_prior_incidents_impl(deps, "Load generator shows non-2xx responses.")

    call = run_subagent.await_args_list[0]
    assert call.kwargs["agent_name"] == "ltm-search"
    assert call.kwargs["tools"] == [read_file, exec_bash_any, grep]


def test_v3_ltm_search_prompt_encourages_read_only_disambiguation() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render(
        "ltm_search_diagnosis_playbooks",
        observed_symptoms="Gateway logs show generic failures.",
        triage_context="",
        diagnosis_playbook_summaries="- slug: example\n  root_cause: Example\n",
    )

    assert "read-only inspection tools available" in prompt
    assert "Use them sparingly" in prompt
    assert "Run 1-3 cheap read-only commands" in prompt
    assert "Do not try to fully prove a diagnosis here" in prompt
    assert "Do not mutate the cluster" in prompt
