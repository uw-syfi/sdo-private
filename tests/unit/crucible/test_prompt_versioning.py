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
    with pytest.raises(ValueError, match="not found"):
        PromptRenderer("nonexistent_version")


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


def test_v3_recovery_mitigation_playbook_prompt_requires_concrete_fix_steps() -> None:
    renderer = PromptRenderer("v3")

    prompt = renderer.render("recovery_mitigation_playbook_system")

    assert "reusable mitigation playbook" in prompt
    assert "concrete resource and field changes" in prompt
    assert "verification_checks" in prompt
    assert "rollback_stop_conditions" in prompt
