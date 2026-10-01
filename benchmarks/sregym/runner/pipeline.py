"""Multi-stage experiment pipeline configuration for SREGym.

Supports running multiple experiments in sequence with automatic
knowledge base chaining — one stage's KB output becomes the next
stage's seed.

Pipeline TOML format uses ``[[stages]]`` (array of tables) with an
optional ``[defaults]`` section for shared config.  Detection vs
single-experiment configs: ``"stages" in raw_toml``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
from pathlib import Path
from typing import Any, cast

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from benchmarks.sregym.runner.experiment import (
    ExperimentConfig,
    RunnerEnv,
    promote_crucible_legacy_config,
    variant_config_from_raw,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StageConfig:
    """One stage in a pipeline."""

    name: str = ""
    chain_kb: bool = True
    chain_application_workspace: bool = False
    runner_overrides: dict[str, Any] = dataclasses.field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]


@dataclasses.dataclass
class PipelineConfig:
    """Multi-stage experiment pipeline."""

    name: str = ""
    defaults: dict[str, Any] = dataclasses.field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    stages: list[StageConfig] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("Pipeline must have at least one stage")
        names = [s.name for s in self.stages if s.name]
        if len(names) != len(set(names)):
            raise ValueError(f"Stage names must be unique, got duplicates: {names}")


@dataclasses.dataclass
class StageState:
    """Runtime state for one pipeline stage."""

    index: int
    name: str
    status: str = "pending"  # pending | running | completed | failed
    experiment_dir: str = ""
    error: str = ""


@dataclasses.dataclass
class PipelineState:
    """Persisted pipeline state for resume support."""

    stages: list[StageState] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def is_pipeline_config(path: Path) -> bool:
    """Return True if *path* is a pipeline TOML (has ``[[stages]]``)."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return "stages" in raw


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_pipeline_config(path: Path) -> PipelineConfig:
    """Parse a pipeline TOML file into a :class:`PipelineConfig`."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    pipeline_raw = raw.get("pipeline", {})
    defaults_raw = raw.get("defaults", {})
    stages_raw = raw.get("stages", [])

    stages: list[StageConfig] = []
    for s in stages_raw:
        s = dict(s)  # copy so we can pop
        name = s.pop("name", "")
        chain_kb = s.pop("chain_kb", True)
        chain_application_workspace = s.pop("chain_application_workspace", False)
        runner_overrides = s.pop("runner", {})
        # Anything remaining under the stage entry (e.g. agent_config)
        # gets merged into runner_overrides.
        for k, v in s.items():
            runner_overrides[k] = v
        stages.append(
            StageConfig(
                name=name,
                chain_kb=chain_kb,
                chain_application_workspace=chain_application_workspace,
                runner_overrides=runner_overrides,
            )
        )

    return PipelineConfig(
        name=pipeline_raw.get("name", ""),
        defaults=defaults_raw,
        stages=stages,
    )


# ---------------------------------------------------------------------------
# Deep merge + ExperimentConfig construction
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into a copy of *base*.

    For nested dicts, merging is recursive.  All other values are
    replaced by the override.
    """
    result: dict[str, Any] = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(
                cast("dict[str, Any]", result[key]),
                cast("dict[str, Any]", val),
            )
        else:
            result[key] = copy.deepcopy(val)
    return result


def merge_stage_config(
    defaults: dict[str, Any],
    overrides: dict[str, Any],
) -> ExperimentConfig:
    """Deep-merge *defaults* with stage *overrides* → ExperimentConfig."""
    merged = _deep_merge(defaults, overrides)

    variants_raw = merged.pop("variants", {})
    env_raw = merged.pop("env", {})
    agent_config = merged.pop("agent_config", {})
    agent = merged.get("agent", "crucible")
    enable_summary = merged.get("enable_summary", True)
    no_inject_summary = merged.get("no_inject_summary", True)
    agent_config = promote_crucible_legacy_config(
        agent=agent,
        agent_config=agent_config,
        enable_summary=enable_summary,
        no_inject_summary=no_inject_summary,
        crucible_seed_kb_dir=str(env_raw.get("crucible_seed_kb_dir", "")),
    )

    variants = variant_config_from_raw(variants_raw)

    return ExperimentConfig(
        agent=agent,
        model=merged.get("model", "google-vertex:gemini-2.5-flash"),
        parallel=merged.get("parallel", 4),
        agent_timeout=merged.get("agent_timeout", 1800),
        app_filter=merged.get("app_filter", ""),
        deploy_from_source=merged.get("deploy_from_source", False),
        application_workspace=merged.get("application_workspace", False),
        enable_summary=enable_summary,
        no_inject_summary=no_inject_summary,
        repeat=merged.get("repeat", 1),
        sequence_len=merged.get("sequence_len", 0),
        sequence_seed=merged.get("sequence_seed", 42),
        require_strict_receipt=merged.get("require_strict_receipt", False),
        allow_failed_verdicts=merged.get("allow_failed_verdicts", False),
        reasoning_effort=str(merged.get("reasoning_effort", "")),
        tasklist=merged.get("tasklist", ""),
        problems=merged.get("problems", []),
        spec_names=merged.get("spec_names", []),
        variants=variants,
        env=RunnerEnv(
            judge_model_id=env_raw.get("judge_model_id", ""),
            crucible_seed_kb_dir=env_raw.get("crucible_seed_kb_dir", ""),
            worker_cpu_limit=str(env_raw.get("worker_cpu_limit", "")),
            reuse_cluster=bool(env_raw.get("reuse_cluster", False)),
            force_recreate_cluster=bool(env_raw.get("force_recreate_cluster", False)),
            preserve_infrastructure=bool(env_raw.get("preserve_infrastructure", False)),
            submit_done_returns_feedback=bool(env_raw.get("submit_done_returns_feedback", False)),
            defer_diagnosis_grading=bool(env_raw.get("defer_diagnosis_grading", False)),
            lifecycle_validation_cache=bool(env_raw.get("lifecycle_validation_cache", False)),
            source_build_cache=bool(env_raw.get("source_build_cache", False)),
            fast_namespace_teardown=bool(env_raw.get("fast_namespace_teardown", False)),
            cleanup_defer_timeout_seconds=int(env_raw.get("cleanup_defer_timeout_seconds", 0)),
            docker_builder=str(env_raw.get("docker_builder", "")),
            kind_worker_nodes=int(env_raw.get("kind_worker_nodes", 0)),
        ),
        agent_config=agent_config,
    )


# ---------------------------------------------------------------------------
# Pipeline state persistence
# ---------------------------------------------------------------------------

_STATE_FILENAME = "pipeline_state.json"
_SNAPSHOT_FILENAME = "pipeline_config.toml"


def write_pipeline_state(state: PipelineState, pipeline_dir: Path) -> Path:
    """Write pipeline state as JSON."""
    dest = pipeline_dir / _STATE_FILENAME
    data = {
        "stages": [dataclasses.asdict(s) for s in state.stages],
    }
    dest.write_text(json.dumps(data, indent=2) + "\n")
    return dest


def read_pipeline_state(pipeline_dir: Path) -> PipelineState:
    """Read pipeline state from JSON."""
    path = pipeline_dir / _STATE_FILENAME
    data = json.loads(path.read_text())
    stages = [StageState(**s) for s in data["stages"]]
    return PipelineState(stages=stages)


def reconcile_pipeline_state(config: PipelineConfig, state: PipelineState) -> PipelineState:
    """Ensure persisted state covers the current config's stage list.

    This supports resuming an existing pipeline directory after the
    pipeline snapshot has been edited to add new downstream stages.
    Existing stage runtime data is preserved; missing stages are
    appended as pending entries. Extra stale state entries are left
    untouched so older run metadata is not discarded implicitly.
    """
    for i, stage_cfg in enumerate(config.stages):
        stage_name = stage_cfg.name or f"stage_{i}"
        if i < len(state.stages):
            state.stages[i].index = i
            state.stages[i].name = stage_name
            continue
        state.stages.append(StageState(index=i, name=stage_name))

    return state


def has_pipeline_state(pipeline_dir: Path) -> bool:
    """Return True if *pipeline_dir* contains a pipeline state file."""
    return (pipeline_dir / _STATE_FILENAME).exists()


# ---------------------------------------------------------------------------
# Pipeline config snapshot
# ---------------------------------------------------------------------------


def write_pipeline_snapshot(config: PipelineConfig, pipeline_dir: Path) -> Path:
    """Write the pipeline config as a TOML snapshot in the pipeline dir."""
    dest = pipeline_dir / _SNAPSHOT_FILENAME
    dest.write_text(_serialize_pipeline_config(config))
    return dest


def read_pipeline_snapshot(pipeline_dir: Path) -> PipelineConfig:
    """Read the pipeline config from an existing pipeline directory."""
    return load_pipeline_config(pipeline_dir / _SNAPSHOT_FILENAME)


# ---------------------------------------------------------------------------
# Stage rerun support
# ---------------------------------------------------------------------------


def reset_stages_for_rerun(
    config: PipelineConfig,
    state: PipelineState,
    from_stage: int,
    pipeline_dir: Path,
) -> PipelineState:
    """Reset stage *from_stage* and downstream chained stages for rerun.

    Renames existing stage directories with a timestamp suffix and sets
    their status back to ``"pending"``.  Stages after *from_stage* that
    have ``chain_kb=false`` are left as-is (they are independent).

    Returns the updated pipeline state.
    """
    reconcile_pipeline_state(config, state)

    if from_stage < 0 or from_stage >= len(config.stages):
        raise ValueError(f"Stage index {from_stage} out of range (0..{len(config.stages) - 1})")

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Determine which stages to reset: from_stage itself, plus any
    # subsequent stages that transitively chain via chain_kb=true.
    to_reset = {from_stage}
    for i in range(from_stage + 1, len(config.stages)):
        if config.stages[i].chain_kb or config.stages[i].chain_application_workspace:
            to_reset.add(i)
        else:
            # Independent stage — stop the chain propagation.
            # But continue scanning in case a later stage chains from
            # a stage that we *are* resetting.
            pass

    for i in to_reset:
        stage_state = state.stages[i]
        if stage_state.experiment_dir:
            old_dir = Path(stage_state.experiment_dir)
            if old_dir.exists():
                backup = old_dir.parent / f"{old_dir.name}.{timestamp}"
                shutil.move(str(old_dir), str(backup))
        stage_state.status = "pending"
        stage_state.experiment_dir = ""
        stage_state.error = ""

    return state


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _toml_value(value: bool | int | str | list[Any]) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    items = ", ".join(_toml_value(item) for item in value)
    return f"[{items}]"


def _serialize_dict_section(d: dict[str, Any], prefix: str) -> list[str]:
    """Serialize a dict as TOML key=value lines, handling nested dicts."""
    lines: list[str] = []
    simple: dict[str, Any] = {}
    nested: dict[str, dict[str, Any]] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            nested[k] = v
        else:
            simple[k] = v

    for k, v in simple.items():
        lines.append(f"{k} = {_toml_value(v)}")

    for k, v in nested.items():
        section = f"{prefix}.{k}" if prefix else k
        lines.append("")
        lines.append(f"[{section}]")
        lines.extend(_serialize_dict_section(v, section))

    return lines


def _serialize_pipeline_config(config: PipelineConfig) -> str:
    lines: list[str] = []

    lines.append("[pipeline]")
    lines.append(f"name = {_toml_value(config.name)}")

    if config.defaults:
        lines.append("")
        lines.append("[defaults]")
        lines.extend(_serialize_dict_section(config.defaults, "defaults"))

    for stage in config.stages:
        lines.append("")
        lines.append("[[stages]]")
        if stage.name:
            lines.append(f"name = {_toml_value(stage.name)}")
        lines.append(f"chain_kb = {_toml_value(stage.chain_kb)}")
        lines.append(f"chain_application_workspace = {_toml_value(stage.chain_application_workspace)}")
        if stage.runner_overrides:
            lines.append("")
            lines.append("[stages.runner]")
            lines.extend(_serialize_dict_section(stage.runner_overrides, "stages.runner"))

    lines.append("")
    return "\n".join(lines) + "\n"
