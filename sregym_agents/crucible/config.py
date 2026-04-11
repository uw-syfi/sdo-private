"""Crucible run configuration and parsing from experiment JSON, CLI, or KB tasks."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping


@dataclasses.dataclass(frozen=True)
class CrucibleConfig:
    enable_judge: bool = True
    enable_ltm_retrieval: bool = False
    enable_ltm_verified_direct_submit: bool = False
    include_benchmark_results: bool = False
    enable_reflection: bool = True
    enable_playbooks: bool = False
    enable_playbook_shortcut: bool = False
    recovery_phase2_enabled: bool = False
    include_incident_files: bool = True
    per_app: bool = True
    prompt_version: str = "v2"
    max_diagnosis_iterations: int = 5
    max_mitigation_iterations: int = 5
    wait_stage_timeout: int = 300
    stage_timeout: int = 900  # 15 minutes max per diagnosis/mitigation stage
    backend: str = "pydantic-ai"  # "pydantic-ai" or "agent-cli"
    agent_cli_provider: str = "claude"  # provider for agent-cli backend

    def __post_init__(self) -> None:
        if self.backend not in ("pydantic-ai", "agent-cli"):
            raise ValueError(f"backend must be 'pydantic-ai' or 'agent-cli', got {self.backend!r}")


def _reflection_from_mapping(mapping: Mapping[str, Any]) -> bool:
    """Resolve enable_reflection with legacy alias enable_heuristic_refinement."""
    if "enable_reflection" in mapping:
        return bool(mapping["enable_reflection"])
    if "enable_heuristic_refinement" in mapping:
        return bool(mapping["enable_heuristic_refinement"])
    return True


def crucible_config_from_experiment_agent(
    agent_settings: Mapping[str, Any],
    *,
    cli_args: argparse.Namespace | None = None,
) -> CrucibleConfig:
    """Merge experiment ``agent`` JSON (from ``SREGYM_EXPERIMENT_AGENT_CONFIG``) with CLI overrides.

    Precedence: CLI wins over ``agent_settings`` for ``prompt_version`` and ``enable_judge``
    (via ``--prompt-version`` and ``--no-judge``).

    Raises:
        ValueError: if ``prompt_version`` is missing after merge (required for the driver).
    """
    prompt_version: str | None = None
    if cli_args is not None and getattr(cli_args, "prompt_version", None):
        prompt_version = cli_args.prompt_version
    if not prompt_version:
        pv = agent_settings.get("prompt_version")
        prompt_version = str(pv) if pv else None
    if not prompt_version:
        raise ValueError(
            "prompt_version is required. Set it in [agent.crucible] config "
            "or pass --prompt-version on the command line."
        )

    enable_judge = bool(agent_settings.get("enable_judge", True))
    if cli_args is not None and getattr(cli_args, "no_judge", False):
        enable_judge = False

    base = CrucibleConfig()
    return dataclasses.replace(
        base,
        enable_judge=enable_judge,
        enable_ltm_retrieval=bool(agent_settings.get("enable_ltm_retrieval", base.enable_ltm_retrieval)),
        enable_ltm_verified_direct_submit=bool(
            agent_settings.get("enable_ltm_verified_direct_submit", base.enable_ltm_verified_direct_submit)
        ),
        include_benchmark_results=bool(agent_settings.get("include_benchmark_results", base.include_benchmark_results)),
        enable_reflection=_reflection_from_mapping(agent_settings),
        enable_playbooks=bool(agent_settings.get("enable_playbooks", base.enable_playbooks)),
        enable_playbook_shortcut=bool(agent_settings.get("enable_playbook_shortcut", base.enable_playbook_shortcut)),
        recovery_phase2_enabled=bool(agent_settings.get("recovery_phase2_enabled", base.recovery_phase2_enabled)),
        include_incident_files=bool(agent_settings.get("include_incident_files", base.include_incident_files)),
        per_app=bool(agent_settings.get("per_app", base.per_app)),
        prompt_version=prompt_version,
        max_diagnosis_iterations=int(agent_settings.get("max_diagnosis_iterations", base.max_diagnosis_iterations)),
        max_mitigation_iterations=int(agent_settings.get("max_mitigation_iterations", base.max_mitigation_iterations)),
        wait_stage_timeout=int(agent_settings.get("wait_stage_timeout", base.wait_stage_timeout)),
        stage_timeout=int(agent_settings.get("stage_timeout", base.stage_timeout)),
        backend=str(agent_settings.get("backend", base.backend)),
        agent_cli_provider=str(agent_settings.get("agent_cli_provider", base.agent_cli_provider)),
    )


def crucible_config_from_kb_task(kb_task: Mapping[str, Any]) -> CrucibleConfig:
    """Build config from a pending KB update task JSON (subset of fields; rest use defaults)."""
    prompt_version = kb_task.get("prompt_version")
    if not prompt_version:
        raise ValueError("KB task is missing prompt_version")

    base = CrucibleConfig()
    return dataclasses.replace(
        base,
        include_benchmark_results=bool(kb_task.get("include_benchmark_results", base.include_benchmark_results)),
        enable_reflection=_reflection_from_mapping(kb_task),
        enable_playbooks=bool(kb_task.get("enable_playbooks", base.enable_playbooks)),
        recovery_phase2_enabled=bool(kb_task.get("recovery_phase2_enabled", base.recovery_phase2_enabled)),
        include_incident_files=bool(kb_task.get("include_incident_files", base.include_incident_files)),
        per_app=bool(kb_task.get("per_app", base.per_app)),
        prompt_version=str(prompt_version),
    )
