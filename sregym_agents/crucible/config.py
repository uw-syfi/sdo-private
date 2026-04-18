"""Crucible run configuration and parsing from experiment JSON, CLI, or KB tasks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping


_PROMPT_VERSION_RE = re.compile(r"^v\d+$")
_PROMPTS_DIR = Path(__file__).parent / "configs" / "prompts"


class CrucibleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enable_judge: bool = True
    enable_ltm_retrieval: bool = False
    enable_ltm_verified_direct_submit: bool = False
    enable_mitigation_kb: bool = True
    include_benchmark_results: bool = False
    kb_scope: Literal["per_app", "shared"] = "per_app"
    kb_runtime_mode: Literal["playbook-first"] = "playbook-first"
    kb_update_mode: Literal["async-review", "inline-review"] = "async-review"
    prompt_version: str = "v2"
    # Explicit capability flags (historically gated by prompt_version >= "v3").
    # When left at ``None``, the post-init validator derives the flag from
    # ``prompt_version == "v3"`` so existing call sites that only set
    # ``prompt_version`` keep working. Pass an explicit ``True``/``False``
    # to override.
    enable_triage_priors: bool | None = None
    enable_success_playbook_candidates: bool | None = None
    enable_mitigation_playbook_curation: bool | None = None
    enable_diagnosis_playbook_candidates: bool | None = None
    max_diagnosis_iterations: int = 5
    max_mitigation_iterations: int = 5
    wait_stage_timeout: int = 300
    stage_timeout: int = 900  # 15 minutes max per diagnosis/mitigation stage
    backend: Literal["pydantic-ai", "agent-cli"] = "pydantic-ai"
    agent_cli_provider: str = "claude"  # provider for agent-cli backend

    @field_validator(
        "max_diagnosis_iterations",
        "max_mitigation_iterations",
        "wait_stage_timeout",
        "stage_timeout",
        mode="before",
    )
    @classmethod
    def _check_positive_int(cls, value: Any, info: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{info.field_name} must be a positive int, got {value!r}")
        return value

    @model_validator(mode="after")
    def _validate_prompt_version_and_derive_flags(self) -> CrucibleConfig:
        if not _PROMPT_VERSION_RE.match(self.prompt_version):
            raise ValueError(
                f"prompt_version must match r'^v\\d+$' (e.g. 'v1', 'v2', 'v3'), got {self.prompt_version!r}"
            )
        prompts_dir = _PROMPTS_DIR / self.prompt_version
        if not prompts_dir.is_dir():
            raise ValueError(f"prompt_version {self.prompt_version!r} has no template directory at {prompts_dir}")
        v3_default = self.prompt_version == "v3"
        for flag in (
            "enable_triage_priors",
            "enable_success_playbook_candidates",
            "enable_mitigation_playbook_curation",
            "enable_diagnosis_playbook_candidates",
        ):
            if getattr(self, flag) is None:
                object.__setattr__(self, flag, v3_default)
        return self

    def to_kb_task_fields(self) -> dict[str, Any]:
        """Return the config subset needed for KB update task serialization."""
        return {
            "include_benchmark_results": self.include_benchmark_results,
            "kb_scope": self.kb_scope,
            "kb_runtime_mode": self.kb_runtime_mode,
            "kb_update_mode": self.kb_update_mode,
            "prompt_version": self.prompt_version,
        }


def _optional_bool(settings: Mapping[str, Any], key: str) -> bool | None:
    """Return ``settings[key]`` as ``bool`` if present, else ``None``.

    ``None`` lets :class:`CrucibleConfig` derive the default from
    ``prompt_version`` in its post-init validator instead of the parser.
    """
    if key not in settings:
        return None
    return bool(settings[key])


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
    return CrucibleConfig(
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
        enable_triage_priors=_optional_bool(agent_settings, "enable_triage_priors"),
        enable_success_playbook_candidates=_optional_bool(agent_settings, "enable_success_playbook_candidates"),
        enable_mitigation_playbook_curation=_optional_bool(agent_settings, "enable_mitigation_playbook_curation"),
        enable_diagnosis_playbook_candidates=_optional_bool(agent_settings, "enable_diagnosis_playbook_candidates"),
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
    # Capability flags are left unset here so CrucibleConfig derives them
    # from ``prompt_version`` in the post-init validator.
    return CrucibleConfig(
        include_benchmark_results=bool(kb_task.get("include_benchmark_results", base.include_benchmark_results)),
        kb_scope=str(kb_task.get("kb_scope", base.kb_scope)),
        kb_runtime_mode=str(kb_task.get("kb_runtime_mode", base.kb_runtime_mode)),
        kb_update_mode=str(kb_task.get("kb_update_mode", base.kb_update_mode)),
        prompt_version=str(prompt_version),
    )
