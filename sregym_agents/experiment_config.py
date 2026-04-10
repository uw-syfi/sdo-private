"""Centralized experiment configuration for SREGym runs.

Provides a single ExperimentConfig dataclass that captures all settings
needed to reproduce an experiment: runner args, env vars, problem selection,
and agent-specific config.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import yaml

_VARIANT_ORDERS = ("flat", "round_robin", "grouped", "adaptive")


@dataclasses.dataclass
class VariantConfig:
    enabled: bool = False
    count: int = 0
    offset: int = 0
    seed: int = 42
    order: str = "round_robin"  # one of _VARIANT_ORDERS
    max_per_class: int | None = None
    consec_solves_to_stop: int | None = None
    spec_names: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    def __post_init__(self) -> None:
        if self.order not in _VARIANT_ORDERS:
            raise ValueError(f"variants.order must be one of {'|'.join(_VARIANT_ORDERS)}, got {self.order!r}")
        if self.max_per_class is not None:
            if self.order not in ("grouped", "adaptive"):
                raise ValueError("variants.max_per_class is only valid when variants.order in {'grouped', 'adaptive'}")
            if self.max_per_class <= 0:
                raise ValueError("variants.max_per_class must be > 0")
        if self.order == "adaptive":
            if self.max_per_class is None:
                raise ValueError("variants.max_per_class is required when variants.order='adaptive'")
            if self.consec_solves_to_stop is None:
                raise ValueError("variants.consec_solves_to_stop is required when variants.order='adaptive'")
            if self.consec_solves_to_stop <= 0:
                raise ValueError("variants.consec_solves_to_stop must be > 0")
        elif self.consec_solves_to_stop is not None:
            raise ValueError("variants.consec_solves_to_stop is only valid when variants.order='adaptive'")
        if self.spec_names and not self.enabled:
            raise ValueError("variants.spec_names requires variants.enabled = true")


@dataclasses.dataclass
class RunnerEnv:
    judge_model_id: str = ""
    crucible_seed_kb_dir: str = ""
    worker_cpu_limit: str = ""


@dataclasses.dataclass
class ExperimentConfig:
    # Runner settings (become CLI args to bench/sregym/main.py)
    agent: str = "crucible"
    model: str = "google-vertex:gemini-2.5-flash"
    parallel: int = 4
    enable_summary: bool = True
    no_inject_summary: bool = True
    repeat: int = 1
    sequence_len: int = 0
    sequence_seed: int = 42

    # Problem selection (mutually exclusive with variants)
    tasklist: str = ""  # named set or path to YAML
    problems: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    # Variant mode
    variants: VariantConfig = dataclasses.field(default_factory=VariantConfig)

    # Env vars passed to the main.py process
    env: RunnerEnv = dataclasses.field(default_factory=RunnerEnv)

    # Agent-specific config (keyed by agent name)
    agent_config: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    def __post_init__(self) -> None:
        if self.variants.enabled and (self.tasklist or self.problems):
            raise ValueError("runner.variants.enabled is mutually exclusive with runner.tasklist and runner.problems")
        if self.tasklist and self.problems:
            raise ValueError("runner.tasklist and runner.problems are mutually exclusive")


def variant_config_from_raw(variants_raw: dict[str, Any]) -> VariantConfig:
    """Build a VariantConfig from a TOML-like dict, accepting the legacy
    ``round_robin`` field on read for backward compatibility with snapshots
    written by older versions of the runner.
    """
    if "order" in variants_raw:
        order = variants_raw["order"]
    elif "round_robin" in variants_raw:
        order = "round_robin" if variants_raw["round_robin"] else "flat"
    else:
        order = "round_robin"

    return VariantConfig(
        enabled=variants_raw.get("enabled", False),
        count=variants_raw.get("count", 0),
        offset=variants_raw.get("offset", 0),
        seed=variants_raw.get("seed", 42),
        order=order,
        max_per_class=variants_raw.get("max_per_class"),
        consec_solves_to_stop=variants_raw.get("consec_solves_to_stop"),
        spec_names=list(variants_raw.get("spec_names", [])),
    )


def load_experiment_config(path: Path) -> ExperimentConfig:
    """Read a TOML experiment config file and return an ExperimentConfig."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    runner = raw.get("runner", {})
    variants_raw = runner.pop("variants", {})
    env_raw = runner.pop("env", {})

    variants = variant_config_from_raw(variants_raw)

    env = RunnerEnv(
        judge_model_id=env_raw.get("judge_model_id", ""),
        crucible_seed_kb_dir=env_raw.get("crucible_seed_kb_dir", ""),
        worker_cpu_limit=str(env_raw.get("worker_cpu_limit", "")),
    )

    agent_config = raw.get("agent", {})

    return ExperimentConfig(
        agent=runner.get("agent", "crucible"),
        model=runner.get("model", "google-vertex:gemini-2.5-flash"),
        parallel=runner.get("parallel", 4),
        enable_summary=runner.get("enable_summary", True),
        no_inject_summary=runner.get("no_inject_summary", True),
        repeat=runner.get("repeat", 1),
        sequence_len=runner.get("sequence_len", 0),
        sequence_seed=runner.get("sequence_seed", 42),
        tasklist=runner.get("tasklist", ""),
        problems=runner.get("problems", []),
        variants=variants,
        env=env,
        agent_config=agent_config,
    )


def resolve_config(
    config: ExperimentConfig,
    env_overrides: dict[str, str] | None = None,
) -> ExperimentConfig:
    """Apply environment variable overrides on top of the loaded config.

    Recognized env vars: MODEL, PARALLEL, JUDGE_MODEL_ID, CRUCIBLE_SEED_KB_DIR,
    SREGYM_WORKER_CPU_LIMIT, SREGYM_PRELOAD_INFRA_IMAGES.
    """
    if env_overrides is None:
        env_overrides = dict(os.environ)

    updates: dict[str, Any] = {}
    env_updates: dict[str, str] = {}

    if "MODEL" in env_overrides:
        updates["model"] = env_overrides["MODEL"]
    if "PARALLEL" in env_overrides:
        updates["parallel"] = int(env_overrides["PARALLEL"])
    if "JUDGE_MODEL_ID" in env_overrides:
        env_updates["judge_model_id"] = env_overrides["JUDGE_MODEL_ID"]
    if "CRUCIBLE_SEED_KB_DIR" in env_overrides:
        env_updates["crucible_seed_kb_dir"] = env_overrides["CRUCIBLE_SEED_KB_DIR"]
    if "SREGYM_WORKER_CPU_LIMIT" in env_overrides:
        env_updates["worker_cpu_limit"] = env_overrides["SREGYM_WORKER_CPU_LIMIT"]
    if updates or env_updates:
        new_env = dataclasses.replace(config.env, **env_updates) if env_updates else config.env
        config = dataclasses.replace(config, **updates, env=new_env)

    return config


_SNAPSHOT_FILENAME = "experiment_config.toml"


def write_snapshot(config: ExperimentConfig, exp_dir: Path) -> Path:
    """Write the resolved config as a TOML file in the experiment directory."""
    dest = exp_dir / _SNAPSHOT_FILENAME
    dest.write_text(_serialize_config(config))
    return dest


def read_snapshot(exp_dir: Path) -> ExperimentConfig:
    """Read an experiment config from an existing experiment directory."""
    return load_experiment_config(exp_dir / _SNAPSHOT_FILENAME)


_TASKLIST_FILENAME = "tasklist.yml"


def resolve_tasklist(
    config: ExperimentConfig,
    sregym_dir: Path,
    exp_dir: Path,
) -> Path | None:
    """Resolve the tasklist and write it into the experiment directory.

    Returns the path to the resolved tasklist YAML, or None if no tasklist
    is needed (variant mode or run-all-problems).
    """
    if config.variants.enabled:
        return None

    dest = exp_dir / _TASKLIST_FILENAME

    if config.problems:
        # Generate tasklist YAML from inline problem list
        tasklist_data = {"all": {"problems": {pid: ["diagnosis", "mitigation"] for pid in config.problems}}}
        with open(dest, "w") as f:
            yaml.dump(tasklist_data, f, default_flow_style=False)
        return dest

    if config.tasklist:
        source = _resolve_tasklist_source(config.tasklist, sregym_dir)
        shutil.copy2(source, dest)
        return dest

    # No tasklist specified = run all problems
    return None


def _resolve_tasklist_source(tasklist_ref: str, sregym_dir: Path) -> Path:
    """Resolve a tasklist reference to an actual file path.

    A tasklist_ref can be:
    - A named pre-built set (e.g. "count_train") that maps to
      sregym/conductor/tasklist.count_train.yml
    - A path to a custom YAML file
    """
    # Check if it's a path to an existing file
    as_path = Path(tasklist_ref)
    if as_path.is_file():
        return as_path

    # Try as a named pre-built set
    prebuilt = sregym_dir / "sregym" / "conductor" / f"tasklist.{tasklist_ref}.yml"
    if prebuilt.is_file():
        return prebuilt

    raise FileNotFoundError(
        f"Tasklist '{tasklist_ref}' not found. Tried: {as_path} (custom path), {prebuilt} (pre-built set)"
    )


_EXTERNAL_AGENTS = {"crucible", "pydantic_agent"}


def config_to_main_args(
    config: ExperimentConfig,
    exp_dir: Path,
    tasklist_path: Path | None,
) -> list[str]:
    """Convert ExperimentConfig to CLI args for bench/sregym/main.py."""
    args = [
        "--agent",
        config.agent,
        "--model",
        config.model,
        "--parallel",
        str(config.parallel),
        "--experiment-dir",
        str(exp_dir),
    ]

    if config.enable_summary:
        args.append("--enable-summary")
    if config.no_inject_summary:
        args.append("--no-inject-summary")
    if config.repeat > 1:
        args.extend(["--repeat", str(config.repeat)])

    if tasklist_path is not None:
        args.extend(["--tasklist", str(tasklist_path)])

    if config.variants.enabled:
        args.append("--variants")
        # In adaptive mode --variant-count is ignored and the runner does not
        # require it; emit it only when set, to keep the CLI clean.
        if config.variants.order != "adaptive" or config.variants.count > 0:
            args.extend(["--variant-count", str(config.variants.count)])
        args.extend(["--variant-offset", str(config.variants.offset)])
        args.extend(["--variant-seed", str(config.variants.seed)])
        args.extend(["--variant-order", config.variants.order])
        if config.variants.max_per_class is not None:
            args.extend(["--variant-max-per-class", str(config.variants.max_per_class)])
        if config.variants.consec_solves_to_stop is not None:
            args.extend(
                [
                    "--variant-adaptive-consec-solves",
                    str(config.variants.consec_solves_to_stop),
                ]
            )
        for name in config.variants.spec_names:
            args.extend(["--variant-spec", name])
    elif config.sequence_len > 0:
        args.extend(["--sequence-len", str(config.sequence_len)])
        args.extend(["--sequence-seed", str(config.sequence_seed)])

    return args


def config_to_env(config: ExperimentConfig, project_root: Path) -> dict[str, str]:
    """Build env var dict from ExperimentConfig.

    Starts with the current environment and adds/overrides entries.
    """
    env = dict(os.environ)

    # Agent registry
    if config.agent in _EXTERNAL_AGENTS:
        registry = project_root / "sregym_agents" / "agents.yaml"
        env["SREGYM_AGENT_REGISTRY"] = str(registry)

    # Runner env vars
    if config.env.judge_model_id:
        env["JUDGE_MODEL_ID"] = config.env.judge_model_id
    if config.env.crucible_seed_kb_dir:
        env["CRUCIBLE_SEED_KB_DIR"] = config.env.crucible_seed_kb_dir
    if config.env.worker_cpu_limit:
        env["SREGYM_WORKER_CPU_LIMIT"] = config.env.worker_cpu_limit

    env["SREGYM_PROGRESS_MODE"] = "rich"

    # Agent-specific config via JSON env var
    agent_cfg = config.agent_config.get(config.agent)
    if agent_cfg:
        env["SREGYM_EXPERIMENT_AGENT_CONFIG"] = json.dumps(agent_cfg)

    return env


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


def _serialize_config(config: ExperimentConfig) -> str:
    """Serialize ExperimentConfig to TOML string."""
    lines = ["[runner]"]
    lines.append(f"agent = {_toml_value(config.agent)}")
    lines.append(f"model = {_toml_value(config.model)}")
    lines.append(f"parallel = {_toml_value(config.parallel)}")
    lines.append(f"enable_summary = {_toml_value(config.enable_summary)}")
    lines.append(f"no_inject_summary = {_toml_value(config.no_inject_summary)}")
    lines.append(f"repeat = {_toml_value(config.repeat)}")
    lines.append(f"sequence_len = {_toml_value(config.sequence_len)}")
    lines.append(f"sequence_seed = {_toml_value(config.sequence_seed)}")

    if config.tasklist:
        lines.append(f"tasklist = {_toml_value(config.tasklist)}")
    if config.problems:
        lines.append(f"problems = {_toml_value(config.problems)}")

    lines.append("")
    lines.append("[runner.variants]")
    lines.append(f"enabled = {_toml_value(config.variants.enabled)}")
    lines.append(f"count = {_toml_value(config.variants.count)}")
    lines.append(f"offset = {_toml_value(config.variants.offset)}")
    lines.append(f"seed = {_toml_value(config.variants.seed)}")
    lines.append(f"order = {_toml_value(config.variants.order)}")
    if config.variants.max_per_class is not None:
        lines.append(f"max_per_class = {_toml_value(config.variants.max_per_class)}")
    if config.variants.consec_solves_to_stop is not None:
        lines.append(f"consec_solves_to_stop = {_toml_value(config.variants.consec_solves_to_stop)}")
    if config.variants.spec_names:
        lines.append(f"spec_names = {_toml_value(config.variants.spec_names)}")

    lines.append("")
    lines.append("[runner.env]")
    lines.append(f"judge_model_id = {_toml_value(config.env.judge_model_id)}")
    lines.append(f"crucible_seed_kb_dir = {_toml_value(config.env.crucible_seed_kb_dir)}")
    lines.append(f"worker_cpu_limit = {_toml_value(config.env.worker_cpu_limit)}")

    for agent_name, agent_cfg in config.agent_config.items():
        lines.append("")
        lines.append(f"[agent.{agent_name}]")
        for k, v in agent_cfg.items():
            lines.append(f"{k} = {_toml_value(v)}")

    lines.append("")
    return "\n".join(lines) + "\n"
