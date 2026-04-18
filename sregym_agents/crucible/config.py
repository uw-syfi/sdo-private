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
    enable_mitigation_kb: bool = True
    include_benchmark_results: bool = False
    kb_scope: str = "per_app"
    kb_runtime_mode: str = "playbook-first"
    kb_update_mode: str = "async-review"
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
        if self.kb_scope not in ("per_app", "shared"):
            raise ValueError(f"kb_scope must be 'per_app' or 'shared', got {self.kb_scope!r}")
        if self.kb_runtime_mode != "playbook-first":
            raise ValueError(f"kb_runtime_mode must be 'playbook-first', got {self.kb_runtime_mode!r}")
        if self.kb_update_mode not in ("async-review", "inline-review"):
            raise ValueError(f"kb_update_mode must be async-review or inline-review, got {self.kb_update_mode!r}")
        for name in (
            "max_diagnosis_iterations",
            "max_mitigation_iterations",
            "wait_stage_timeout",
            "stage_timeout",
        ):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ValueError(f"{name} must be a positive int, got {val!r}")

    def to_kb_task_fields(self) -> dict[str, Any]:
        """Return the config subset needed for KB update task serialization."""
        return {
            "include_benchmark_results": self.include_benchmark_results,
            "kb_scope": self.kb_scope,
            "kb_runtime_mode": self.kb_runtime_mode,
            "kb_update_mode": self.kb_update_mode,
            "prompt_version": self.prompt_version,
        }


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
        enable_mitigation_kb=bool(agent_settings.get("enable_mitigation_kb", base.enable_mitigation_kb)),
        include_benchmark_results=bool(agent_settings.get("include_benchmark_results", base.include_benchmark_results)),
        kb_scope=str(agent_settings.get("kb_scope", base.kb_scope)),
        kb_runtime_mode=str(agent_settings.get("kb_runtime_mode", base.kb_runtime_mode)),
        kb_update_mode=str(agent_settings.get("kb_update_mode", base.kb_update_mode)),
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
        kb_scope=str(kb_task.get("kb_scope", base.kb_scope)),
        kb_runtime_mode=str(kb_task.get("kb_runtime_mode", base.kb_runtime_mode)),
        kb_update_mode=str(kb_task.get("kb_update_mode", base.kb_update_mode)),
        prompt_version=str(prompt_version),
    )
