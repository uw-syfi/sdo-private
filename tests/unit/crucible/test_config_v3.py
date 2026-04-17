from __future__ import annotations

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
    cfg = crucible_config_from_experiment_agent(
        {"prompt_version": "v3", "enable_mitigation_kb": False}
    )
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
