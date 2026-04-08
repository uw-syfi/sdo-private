"""Tests for sregym_agents.crucible.config."""

from __future__ import annotations

import argparse

import pytest

from sregym_agents.crucible.config import (
    CrucibleConfig,
    crucible_config_from_experiment_agent,
    crucible_config_from_kb_task,
)


def test_experiment_agent_defaults_and_cli_prompt_version() -> None:
    agent: dict = {}
    cli = argparse.Namespace(prompt_version="v3", no_judge=False)
    cfg = crucible_config_from_experiment_agent(agent, cli_args=cli)
    assert cfg.prompt_version == "v3"
    assert cfg.enable_judge is True
    assert cfg.enable_ltm_retrieval is False


def test_experiment_agent_prompt_from_agent_dict() -> None:
    agent = {"prompt_version": "v1", "enable_judge": False}
    cfg = crucible_config_from_experiment_agent(agent, cli_args=None)
    assert cfg.prompt_version == "v1"
    assert cfg.enable_judge is False


def test_experiment_cli_overrides_prompt_and_no_judge() -> None:
    agent = {"prompt_version": "v1", "enable_judge": True}
    cli = argparse.Namespace(prompt_version="v2", no_judge=True)
    cfg = crucible_config_from_experiment_agent(agent, cli_args=cli)
    assert cfg.prompt_version == "v2"
    assert cfg.enable_judge is False


def test_experiment_enable_heuristic_refinement_alias() -> None:
    agent = {"prompt_version": "v1", "enable_heuristic_refinement": False}
    cfg = crucible_config_from_experiment_agent(agent, cli_args=None)
    assert cfg.enable_reflection is False


def test_experiment_enable_reflection_wins_over_heuristic() -> None:
    agent = {
        "prompt_version": "v1",
        "enable_reflection": True,
        "enable_heuristic_refinement": False,
    }
    cfg = crucible_config_from_experiment_agent(agent, cli_args=None)
    assert cfg.enable_reflection is True


def test_experiment_recovery_phase2_enabled() -> None:
    agent = {
        "prompt_version": "v1",
        "recovery_phase2_enabled": True,
    }
    cfg = crucible_config_from_experiment_agent(agent, cli_args=None)
    assert cfg.recovery_phase2_enabled is True


def test_experiment_missing_prompt_version_raises() -> None:
    with pytest.raises(ValueError, match="prompt_version is required"):
        crucible_config_from_experiment_agent({}, cli_args=None)


def test_experiment_numeric_fields() -> None:
    agent = {
        "prompt_version": "v1",
        "max_diagnosis_iterations": 7,
        "stage_timeout": 600,
    }
    cfg = crucible_config_from_experiment_agent(agent, cli_args=None)
    assert cfg.max_diagnosis_iterations == 7
    assert cfg.stage_timeout == 600


def test_kb_task_minimal() -> None:
    task = {
        "prompt_version": "v2",
        "kb_type": "structured",
        "kb_dir": "/tmp",
        "model_id": "m",
        "app_name": "a",
    }
    cfg = crucible_config_from_kb_task(task)
    assert cfg.prompt_version == "v2"
    assert cfg.include_incident_files is True
    assert cfg.enable_reflection is True


def test_kb_task_heuristic_alias() -> None:
    task = {
        "prompt_version": "v1",
        "enable_heuristic_refinement": False,
    }
    cfg = crucible_config_from_kb_task(task)
    assert cfg.enable_reflection is False


def test_kb_task_recovery_phase2_enabled() -> None:
    task = {
        "prompt_version": "v1",
        "recovery_phase2_enabled": True,
    }
    cfg = crucible_config_from_kb_task(task)
    assert cfg.recovery_phase2_enabled is True


def test_kb_task_missing_prompt_version_raises() -> None:
    with pytest.raises(ValueError, match="missing prompt_version"):
        crucible_config_from_kb_task({})


def test_kb_task_matches_defaults_where_unset() -> None:
    task = {"prompt_version": "v9"}
    cfg = crucible_config_from_kb_task(task)
    base = CrucibleConfig(prompt_version="v9")
    assert cfg == base
