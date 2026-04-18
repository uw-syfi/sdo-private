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
