from __future__ import annotations

from typing import Any

import pytest

from sregym_agents.crucible.config import (
    CrucibleConfig,
    crucible_config_from_experiment_agent,
    crucible_config_from_kb_task,
)


def test_crucible_config_defaults_are_v3_root_cause() -> None:
    cfg = CrucibleConfig()
    assert cfg.kb_scope == "per_app"
    assert cfg.kb_runtime_mode == "playbook-first"
    assert cfg.kb_update_mode == "async-review"


def test_crucible_config_rejects_invalid_scope() -> None:
    with pytest.raises(ValueError, match="kb_scope"):
        CrucibleConfig(kb_scope="bad")


def test_config_from_experiment_reads_kb_v3_fields() -> None:
    cfg = crucible_config_from_experiment_agent(
        {
            "prompt_version": "v3",
            "kb_scope": "shared",
            "kb_runtime_mode": "playbook-first",
            "kb_update_mode": "async-review",
        }
    )
    assert cfg.kb_scope == "shared"


def test_crucible_config_enable_mitigation_kb_default_true() -> None:
    cfg = CrucibleConfig()
    assert cfg.enable_mitigation_kb is True


def test_config_from_experiment_reads_enable_mitigation_kb() -> None:
    cfg = crucible_config_from_experiment_agent({"prompt_version": "v3", "enable_mitigation_kb": False})
    assert cfg.enable_mitigation_kb is False


def test_config_from_kb_task_reads_kb_v3_fields() -> None:
    cfg = crucible_config_from_kb_task(
        {
            "prompt_version": "v3",
            "kb_scope": "shared",
            "kb_runtime_mode": "playbook-first",
            "kb_update_mode": "async-review",
        }
    )
    assert cfg.kb_scope == "shared"


NUMERIC_FIELDS = (
    "max_diagnosis_iterations",
    "max_mitigation_iterations",
    "wait_stage_timeout",
    "stage_timeout",
)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_rejects_zero_numeric(field: str) -> None:
    kwargs: dict[str, Any] = {field: 0}
    with pytest.raises(ValueError, match=f"{field} must be a positive int"):
        CrucibleConfig(**kwargs)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_rejects_negative_numeric(field: str) -> None:
    kwargs: dict[str, Any] = {field: -1}
    with pytest.raises(ValueError, match=f"{field} must be a positive int"):
        CrucibleConfig(**kwargs)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_rejects_string_numeric(field: str) -> None:
    kwargs: dict[str, Any] = {field: "5"}
    with pytest.raises(ValueError, match=f"{field} must be a positive int"):
        CrucibleConfig(**kwargs)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_rejects_float_numeric(field: str) -> None:
    kwargs: dict[str, Any] = {field: 5.0}
    with pytest.raises(ValueError, match=f"{field} must be a positive int"):
        CrucibleConfig(**kwargs)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_rejects_bool_numeric(field: str) -> None:
    kwargs: dict[str, Any] = {field: True}
    with pytest.raises(ValueError, match=f"{field} must be a positive int"):
        CrucibleConfig(**kwargs)


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_crucible_config_accepts_positive_int(field: str) -> None:
    kwargs: dict[str, Any] = {field: 7}
    cfg = CrucibleConfig(**kwargs)
    assert getattr(cfg, field) == 7


def test_crucible_config_accepts_default_numerics() -> None:
    cfg = CrucibleConfig()
    assert cfg.max_diagnosis_iterations == 5
    assert cfg.max_mitigation_iterations == 5
    assert cfg.wait_stage_timeout == 300
    assert cfg.stage_timeout == 900
# -- Feature flags + prompt_version validation (issue #88) --


def test_crucible_config_rejects_non_vN_prompt_version() -> None:
    with pytest.raises(ValueError, match="prompt_version"):
        CrucibleConfig(prompt_version="nonsense")


def test_crucible_config_rejects_empty_prompt_version() -> None:
    with pytest.raises(ValueError, match="prompt_version"):
        CrucibleConfig(prompt_version="")


def test_crucible_config_rejects_prompt_version_with_missing_template_dir() -> None:
    with pytest.raises(ValueError, match="v999"):
        CrucibleConfig(prompt_version="v999")


def test_crucible_config_accepts_v1_v2_v3_prompt_versions() -> None:
    # All three versions have template directories shipped with the repo.
    for pv in ("v1", "v2", "v3"):
        cfg = CrucibleConfig(prompt_version=pv)
        assert cfg.prompt_version == pv


def test_flags_default_off_for_v2_prompt_version() -> None:
    cfg = CrucibleConfig(prompt_version="v2")
    assert cfg.enable_triage_priors is False
    assert cfg.enable_success_playbook_candidates is False
    assert cfg.enable_mitigation_playbook_curation is False
    assert cfg.enable_diagnosis_playbook_candidates is False


def test_flags_can_be_overridden_explicitly() -> None:
    cfg = CrucibleConfig(
        prompt_version="v2",
        enable_triage_priors=True,
        enable_success_playbook_candidates=True,
        enable_mitigation_playbook_curation=True,
        enable_diagnosis_playbook_candidates=True,
    )
    assert cfg.enable_triage_priors is True
    assert cfg.enable_success_playbook_candidates is True
    assert cfg.enable_mitigation_playbook_curation is True
    assert cfg.enable_diagnosis_playbook_candidates is True


def test_config_from_experiment_enables_flags_for_v3() -> None:
    cfg = crucible_config_from_experiment_agent({"prompt_version": "v3"})
    assert cfg.prompt_version == "v3"
    assert cfg.enable_triage_priors is True
    assert cfg.enable_success_playbook_candidates is True
    assert cfg.enable_mitigation_playbook_curation is True
    assert cfg.enable_diagnosis_playbook_candidates is True


def test_config_from_experiment_disables_flags_for_non_v3() -> None:
    cfg = crucible_config_from_experiment_agent({"prompt_version": "v2"})
    assert cfg.prompt_version == "v2"
    assert cfg.enable_triage_priors is False
    assert cfg.enable_success_playbook_candidates is False
    assert cfg.enable_mitigation_playbook_curation is False
    assert cfg.enable_diagnosis_playbook_candidates is False


def test_config_from_experiment_respects_explicit_flag_overrides() -> None:
    cfg = crucible_config_from_experiment_agent(
        {
            "prompt_version": "v3",
            "enable_triage_priors": False,
            "enable_success_playbook_candidates": False,
            "enable_mitigation_playbook_curation": False,
            "enable_diagnosis_playbook_candidates": False,
        }
    )
    assert cfg.enable_triage_priors is False
    assert cfg.enable_success_playbook_candidates is False
    assert cfg.enable_mitigation_playbook_curation is False
    assert cfg.enable_diagnosis_playbook_candidates is False


def test_config_from_experiment_can_enable_flags_without_v3() -> None:
    cfg = crucible_config_from_experiment_agent(
        {
            "prompt_version": "v2",
            "enable_triage_priors": True,
            "enable_diagnosis_playbook_candidates": True,
        }
    )
    assert cfg.prompt_version == "v2"
    assert cfg.enable_triage_priors is True
    assert cfg.enable_diagnosis_playbook_candidates is True
    # Un-overridden flags still default off.
    assert cfg.enable_success_playbook_candidates is False
    assert cfg.enable_mitigation_playbook_curation is False
