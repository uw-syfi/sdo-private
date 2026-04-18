"""Tests for prompt version loading in sregym_agents.crucible._prompts."""

from __future__ import annotations

import pytest

from sregym_agents.crucible._prompts import PromptRenderer


def test_configure_valid_version() -> None:
    renderer = PromptRenderer("v1")
    result = renderer.render("diagnosis_agent_system")
    assert isinstance(result, str)
    assert len(result) > 0


def test_configure_invalid_version() -> None:
    # Existence is validated at CrucibleConfig construction time; a direct
    # PromptRenderer with a missing version surfaces the error lazily when
    # rendering a template.
    renderer = PromptRenderer("nonexistent_version")
    import jinja2

    with pytest.raises(jinja2.TemplateNotFound):
        renderer.render("diagnosis_agent_system")


def test_render_raises_on_missing_variable() -> None:
    renderer = PromptRenderer("v1")
    import jinja2

    with pytest.raises(jinja2.UndefinedError):
        # diagnosis_agent_user requires many variables — omit most to trigger the error.
        renderer.render("diagnosis_agent_user", app_name="test", namespace="test")


def test_v3_recovery_diagnosis_playbook_prompt_requires_localization_first_verification() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render("recovery_diagnosis_playbook_system")

    assert "Do not assume the symptom-bearing component is the faulting component." in prompt
    assert "`fault_localization_checks` must help an agent move from observed symptoms" in prompt
    assert "Structure `fault_localization_checks` as a generic target-selection procedure" in prompt
    assert "map it to the serving or entrypoint component" in prompt
    assert "enumerate downstream dependencies on the active path" in prompt
    assert "prefer role-based placeholders" in prompt
    assert "the chosen target is actually on the failing path" in prompt
    assert "The final diagnosis playbook file should have the following shape" in prompt
    assert "## Fault Localization" in prompt
    assert "`fault_localization_checks` populates `## Fault Localization`" in prompt


def test_v3_diagnosis_prompt_allows_repeat_kb_search_after_new_evidence() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render("diagnosis_agent_system")

    assert "RECOMMENDED WORKFLOW:" in prompt
    assert "Use this as your default sequence" in prompt
    assert "MAY call `search_prior_incidents`" in prompt
    assert "exact error text" in prompt
    assert "affected FQDN" in prompt


def test_v3_recovery_diagnosis_prompt_handles_post_mitigation_state() -> None:
    renderer = PromptRenderer("v3")

    system_prompt = renderer.render("recovery_diagnosis_system")
    user_prompt = renderer.render(
        "recovery_diagnosis_user",
        benchmark_reasoning="The benchmark knows the true root cause.",
        original_answer="The original diagnosis was wrong.",
        original_justification="It focused on a symptom-bearing component.",
        original_causal_chain="wrong target -> wrong conclusion",
        app_name="social-network",
        namespace="social-network",
        descriptions="",
    )

    assert "This recovery run may happen after mitigation has already changed the cluster." in system_prompt
    assert "Do NOT assume the fault is still live." in system_prompt
    assert "If the live fault has already been mitigated" in system_prompt
    assert "This recovery diagnosis may run after mitigation has already changed the cluster." in user_prompt
    assert "rather than insisting on reproducing the fault" in user_prompt


def test_v3_recovery_mitigation_playbook_prompt_requires_concrete_fix_steps() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render("recovery_mitigation_playbook_system")

    assert "reusable mitigation playbook" in prompt
    assert "concrete resource and field changes" in prompt
    assert "verification_checks" in prompt
    assert "rollback_stop_conditions" in prompt
    assert "placeholder_resolution" in prompt
    assert "For every placeholder you introduce" in prompt
    assert "single concrete value or to multiple concrete values" in prompt
    assert "class of values" in prompt


def test_v3_success_diagnosis_playbook_prompt_uses_primary_run_context() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render(
        "success_diagnosis_playbook_user",
        diagnosis_answer="CoreDNS returns NXDOMAIN for post-storage-service.",
        diagnosis_justification="kubectl showed CoreDNS template rules for the failing service FQDN.",
        diagnosis_causal_chain="CoreDNS rule -> NXDOMAIN -> client failures",
        app_name="social-network",
        namespace="social-network",
        descriptions="",
    )

    assert "successful diagnosis" in prompt
    assert "Using your existing investigation context" in prompt
    assert "The grounded diagnosis you just established was" in prompt
    assert "recovery diagnosis" not in prompt.lower()


def test_v3_success_mitigation_playbook_prompt_uses_primary_run_context() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render(
        "success_mitigation_playbook_user",
        root_cause_slug="coredns-nxdomain",
        root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
        diagnosis_answer="CoreDNS returns NXDOMAIN for post-storage-service.",
        mitigation_answer="Patched ConfigMap/coredns to remove the NXDOMAIN template blocks.",
        mitigation_justification="DNS lookups for the affected service names now resolve successfully.",
        app_name="social-network",
        namespace="social-network",
        descriptions="",
    )

    assert "successful mitigation" in prompt
    assert "Using your existing investigation context" in prompt
    assert "The grounded mitigation you just established was" in prompt
    assert "failed or incomplete mitigation" not in prompt.lower()


def test_v3_mitigation_prompt_uses_diagnosis_shared_file_and_faulting_components() -> None:
    renderer = PromptRenderer("v3")

    system_prompt = renderer.render("mitigation_agent_system")
    user_prompt = renderer.render(
        "mitigation_agent_user",
        app_name="social-network",
        namespace="social-network",
        descriptions="",
        iteration=1,
        shared_content="# mitigation state\n",
        shared_file="/tmp/mitigation_session_state.md",
        diagnosis_shared_content="# diagnosis state\n**Diagnosis**: The faulting component is deployment/geo.\n",
        diagnosis_shared_file="/tmp/diagnosis_session_state.md",
        architecture_content="",
        lt_summary_content="",
        lessons_content="",
    )

    assert "Extract the faulting component(s) from diagnosis first" in system_prompt
    assert "Do not assume the symptom-bearing component is the one to patch" in system_prompt
    assert "The diagnosis shared file is at: /tmp/diagnosis_session_state.md" in user_prompt
    assert "The faulting component is deployment/geo." in user_prompt


def test_v3_ltm_apply_mitigation_prompt_reads_diagnosis_shared_file() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render(
        "ltm_apply_mitigation",
        namespace="social-network",
        stage="mitigation",
        strategy_index=0,
        root_cause_class="Misconfigured service port.",
        mitigation_approach="",
        playbook="# Mitigation Playbook\n",
        failed_attempts="",
        diagnosis_shared_file="/tmp/diagnosis_session_state.md",
        diagnosis_shared_content=(
            "# diagnosis state\n**Diagnosis**: The faulting component is service/post-storage-service.\n"
        ),
    )

    assert "Extract the faulting component(s) from the diagnosis shared file first" in prompt
    assert "The diagnosis shared file is at: /tmp/diagnosis_session_state.md" in prompt
    assert "The faulting component is service/post-storage-service." in prompt
    assert "Placeholder Resolution" in prompt
    assert "single concrete value or to multiple concrete values" in prompt
    assert "class of values" in prompt
