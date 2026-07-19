"""Centralized experiment configuration for SREGym runs.

Provides a single ExperimentConfig dataclass that captures all settings
needed to reproduce an experiment: runner args, env vars, problem selection,
and agent-specific config.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator
from pydantic.dataclasses import dataclass

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import yaml

_VARIANT_ORDERS = ("flat", "round_robin", "grouped", "adaptive")
VariantOrder = Literal["flat", "round_robin", "grouped", "adaptive"]
ApplicationWorkspaceMode = Literal["persistent", "ephemeral"]
ApplicationWorkspaceSetting = ApplicationWorkspaceMode | bool


@dataclass
class VariantConfig:
    enabled: bool = False
    count: int = 0
    offset: int = 0
    seed: int = 42
    order: VariantOrder = "round_robin"
    max_per_class: int | None = None
    consec_solves_to_stop: int | None = None
    spec_names: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    @model_validator(mode="after")
    def _validate(self) -> VariantConfig:
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
        return self


_BOOL_ENV_TRUE = {"1", "true", "yes", "on"}


def _parse_bool_env(value: str) -> bool:
    return value.strip().lower() in _BOOL_ENV_TRUE


def application_workspace_enabled(value: ApplicationWorkspaceSetting) -> bool:
    return application_workspace_mode(value) is not None


def application_workspace_mode(value: ApplicationWorkspaceSetting) -> ApplicationWorkspaceMode | None:
    if value is True:
        return "persistent"
    if value is False:
        return None
    return value


@dataclass
class RunnerEnv:
    judge_model_id: str = ""
    crucible_seed_kb_dir: str = ""
    worker_cpu_limit: str = ""
    reuse_cluster: bool = False
    force_recreate_cluster: bool = False
    submit_done_returns_feedback: bool = False
    cleanup_defer_timeout_seconds: int = 0


def promote_crucible_legacy_config(
    *,
    agent: str,
    agent_config: dict[str, dict[str, Any]],
    enable_summary: bool,
    no_inject_summary: bool,
    crucible_seed_kb_dir: str = "",
) -> dict[str, dict[str, Any]]:
    """Move legacy runner/env Crucible settings into ``agent.crucible``.

    Older experiment files stored KB settings under ``[runner]`` and
    ``[runner.env]`` because sregym forwarded them as generic summary flags.
    Crucible now owns those fields, while this promotion keeps old TOMLs and
    snapshots readable.
    """
    normalized = copy.deepcopy(agent_config)
    if agent != "crucible":
        return normalized

    crucible_cfg = normalized.setdefault("crucible", {})
    if "seed_kb_dir" not in crucible_cfg and "crucible_seed_kb_dir" in crucible_cfg:
        crucible_cfg["seed_kb_dir"] = crucible_cfg.pop("crucible_seed_kb_dir")
    crucible_cfg.setdefault("enable_summary", enable_summary)
    crucible_cfg.setdefault("no_inject_summary", no_inject_summary)
    if crucible_seed_kb_dir:
        crucible_cfg.setdefault("seed_kb_dir", crucible_seed_kb_dir)
    return normalized


@dataclass
class ExperimentConfig:
    # Runner settings (become CLI args to bench/sregym/main.py)
    agent: str = "crucible"
    model: str = "google-vertex:gemini-2.5-flash"
    parallel: int = 4
    app_filter: str = ""
    deploy_from_source: bool = False
    application_workspace: ApplicationWorkspaceSetting = False
    enable_summary: bool = True
    no_inject_summary: bool = True
    repeat: int = 1
    sequence_len: int = 0
    sequence_seed: int = 42

    # Problem selection (mutually exclusive with variants)
    tasklist: str = ""  # named set or path to YAML
    problems: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    spec_names: list[str] = dataclasses.field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    # Variant mode
    variants: VariantConfig = dataclasses.field(default_factory=VariantConfig)

    # Env vars passed to the main.py process
    env: RunnerEnv = dataclasses.field(default_factory=RunnerEnv)

    # Agent-specific config (keyed by agent name)
    agent_config: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    @model_validator(mode="after")
    def _validate_mutual_exclusion(self) -> ExperimentConfig:
        if self.variants.enabled and (self.tasklist or self.problems):
            raise ValueError("runner.variants.enabled is mutually exclusive with runner.tasklist and runner.problems")
        if self.tasklist and self.problems:
            raise ValueError("runner.tasklist and runner.problems are mutually exclusive")
        if self.spec_names and self.variants.enabled:
            raise ValueError("runner.spec_names cannot be used with runner.variants.enabled")
        if self.spec_names and (self.tasklist or self.problems):
            raise ValueError("runner.spec_names is mutually exclusive with runner.tasklist and runner.problems")
        if application_workspace_enabled(self.application_workspace):
            if not self.app_filter:
                raise ValueError("application_workspace requires runner.app_filter")
            if not self.deploy_from_source:
                raise ValueError("application_workspace requires runner.deploy_from_source = true")
        return self


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
        reuse_cluster=bool(env_raw.get("reuse_cluster", False)),
        force_recreate_cluster=bool(env_raw.get("force_recreate_cluster", False)),
        submit_done_returns_feedback=bool(env_raw.get("submit_done_returns_feedback", False)),
        cleanup_defer_timeout_seconds=int(env_raw.get("cleanup_defer_timeout_seconds", 0)),
    )

    agent = runner.get("agent", "crucible")
    enable_summary = runner.get("enable_summary", True)
    no_inject_summary = runner.get("no_inject_summary", True)
    agent_config = promote_crucible_legacy_config(
        agent=agent,
        agent_config=raw.get("agent", {}),
        enable_summary=enable_summary,
        no_inject_summary=no_inject_summary,
        crucible_seed_kb_dir=str(env_raw.get("crucible_seed_kb_dir", "")),
    )

    return ExperimentConfig(
        agent=agent,
        model=runner.get("model", "google-vertex:gemini-2.5-flash"),
        parallel=runner.get("parallel", 4),
        app_filter=runner.get("app_filter", ""),
        deploy_from_source=runner.get("deploy_from_source", False),
        application_workspace=runner.get("application_workspace", False),
        enable_summary=enable_summary,
        no_inject_summary=no_inject_summary,
        repeat=runner.get("repeat", 1),
        sequence_len=runner.get("sequence_len", 0),
        sequence_seed=runner.get("sequence_seed", 42),
        tasklist=runner.get("tasklist", ""),
        problems=runner.get("problems", []),
        spec_names=runner.get("spec_names", []),
        variants=variants,
        env=env,
        agent_config=agent_config,
    )


def resolve_config(
    config: ExperimentConfig,
    env_overrides: dict[str, str] | None = None,
) -> ExperimentConfig:
    """Apply environment variable overrides on top of the loaded config.

    Recognized env vars: MODEL, PARALLEL, JUDGE_MODEL_ID,
    SREGYM_WORKER_CPU_LIMIT, SREGYM_REUSE_CLUSTER,
    SREGYM_FORCE_RECREATE_CLUSTER, SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK,
    SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS.
    """
    if env_overrides is None:
        env_overrides = dict(os.environ)

    updates: dict[str, Any] = {}
    env_updates: dict[str, Any] = {}

    if "MODEL" in env_overrides:
        updates["model"] = env_overrides["MODEL"]
    if "PARALLEL" in env_overrides:
        updates["parallel"] = int(env_overrides["PARALLEL"])
    if "SREGYM_DEPLOY_FROM_SOURCE" in env_overrides:
        updates["deploy_from_source"] = _parse_bool_env(env_overrides["SREGYM_DEPLOY_FROM_SOURCE"])
    if "JUDGE_MODEL_ID" in env_overrides:
        env_updates["judge_model_id"] = env_overrides["JUDGE_MODEL_ID"]
    if "SREGYM_WORKER_CPU_LIMIT" in env_overrides:
        env_updates["worker_cpu_limit"] = env_overrides["SREGYM_WORKER_CPU_LIMIT"]
    if "SREGYM_REUSE_CLUSTER" in env_overrides:
        env_updates["reuse_cluster"] = _parse_bool_env(env_overrides["SREGYM_REUSE_CLUSTER"])
    if "SREGYM_FORCE_RECREATE_CLUSTER" in env_overrides:
        env_updates["force_recreate_cluster"] = _parse_bool_env(env_overrides["SREGYM_FORCE_RECREATE_CLUSTER"])
    if "SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK" in env_overrides:
        env_updates["submit_done_returns_feedback"] = _parse_bool_env(
            env_overrides["SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK"]
        )
    if "SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS" in env_overrides:
        env_updates["cleanup_defer_timeout_seconds"] = int(env_overrides["SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS"])
    if updates or env_updates:
        new_env = dataclasses.replace(config.env, **env_updates) if env_updates else config.env
        config = dataclasses.replace(config, **updates, env=new_env)

    return config


def effective_agent_config(config: ExperimentConfig) -> dict[str, Any]:
    """Return the selected agent's config after legacy promotion."""
    agent_config = promote_crucible_legacy_config(
        agent=config.agent,
        agent_config=config.agent_config,
        enable_summary=config.enable_summary,
        no_inject_summary=config.no_inject_summary,
        crucible_seed_kb_dir=config.env.crucible_seed_kb_dir,
    )
    selected = copy.deepcopy(agent_config.get(config.agent, {}))
    if config.agent == "cli_agent":
        workspace_mode = application_workspace_mode(config.application_workspace)
        if workspace_mode is not None:
            selected["application_workspace_mode"] = workspace_mode
    return selected


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


_EXTERNAL_AGENTS = {"crucible", "pydantic_agent", "cli_agent", "sdo_codex"}


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

    if config.app_filter:
        args.extend(["--app-filter", config.app_filter])
    if config.deploy_from_source:
        args.append("--deploy-from-source")
    # main.py declares --application-workspace as store_true (boolean enable);
    # it takes no value. Emit the bare flag for every enabled mode. The mode
    # (persistent vs ephemeral) is forwarded to the cli_agent via
    # effective_agent_config (application_workspace_mode), not the CLI — a
    # trailing mode token here is an argparse usage error (exit 2).
    if application_workspace_mode(config.application_workspace) is not None:
        args.append("--application-workspace")
    if config.repeat > 1:
        args.extend(["--repeat", str(config.repeat)])

    if tasklist_path is not None:
        args.extend(["--tasklist", str(tasklist_path)])

    for name in config.spec_names:
        args.extend(["--problem-spec", name])

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


def config_to_env(config: ExperimentConfig, project_root: Path, exp_dir: Path | None = None) -> dict[str, str]:
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
    if config.env.worker_cpu_limit:
        env["SREGYM_WORKER_CPU_LIMIT"] = config.env.worker_cpu_limit
    if config.env.reuse_cluster:
        env["SREGYM_REUSE_CLUSTER"] = "1"
    if config.env.force_recreate_cluster:
        env["SREGYM_FORCE_RECREATE_CLUSTER"] = "1"
    env["SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK"] = "1" if config.env.submit_done_returns_feedback else "0"
    if config.env.cleanup_defer_timeout_seconds > 0:
        env["SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS"] = str(config.env.cleanup_defer_timeout_seconds)

    env["SREGYM_PROGRESS_MODE"] = "rich"
    if exp_dir is not None:
        env["SREGYM_EXPERIMENT_DIR"] = str(exp_dir)

    # Agent-specific config via JSON env var
    agent_cfg = effective_agent_config(config)
    if agent_cfg:
        env["SREGYM_EXPERIMENT_AGENT_CONFIG"] = json.dumps(agent_cfg)
    if config.agent == "sdo_codex":
        sdo_cfg = config.agent_config.get("sdo_codex") or {}
        validator_image = str(sdo_cfg.get("validator_image", "sdo-detector-validator:v0.1.0"))
        env["SREGYM_KIND_REQUIRED_IMAGES"] = json.dumps(
            [
                str(sdo_cfg.get("controller_image", "sdo-controller:v0.1.0")),
                str(sdo_cfg.get("responder_image", "sdo-responder:v0.1.0")),
                validator_image,
            ]
        )
        # Production validator Jobs execute untrusted detector code behind a
        # deny-all NetworkPolicy. A CNI that merely accepts NetworkPolicy
        # objects without enforcing them is not a production-equivalent test
        # environment, so SDO workers require a real enforcement preflight.
        env["SREGYM_KIND_REQUIRE_NETWORK_POLICY"] = "1"
        env["SREGYM_KIND_NETWORK_POLICY_CANARY_IMAGE"] = validator_image

    # Promote cli_agent's autonomous_submit flag to a dedicated env var. The
    # MCP server that registers submit_* tools is launched in the worker
    # process before the cli_agent driver starts, so it reads this env var
    # at module load — ``SREGYM_EXPERIMENT_AGENT_CONFIG`` alone would not
    # reach it in time. Conductor and driver also read this flag.
    cli_agent_cfg = config.agent_config.get("cli_agent") or {}
    if cli_agent_cfg.get("autonomous_submit"):
        env["SREGYM_AUTONOMOUS_SUBMIT"] = "1"

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
    lines.append(f"app_filter = {_toml_value(config.app_filter)}")
    lines.append(f"deploy_from_source = {_toml_value(config.deploy_from_source)}")
    lines.append(f"application_workspace = {_toml_value(config.application_workspace)}")
    lines.append(f"repeat = {_toml_value(config.repeat)}")
    lines.append(f"sequence_len = {_toml_value(config.sequence_len)}")
    lines.append(f"sequence_seed = {_toml_value(config.sequence_seed)}")

    if config.tasklist:
        lines.append(f"tasklist = {_toml_value(config.tasklist)}")
    if config.problems:
        lines.append(f"problems = {_toml_value(config.problems)}")
    if config.spec_names:
        lines.append(f"spec_names = {_toml_value(config.spec_names)}")

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
    lines.append(f"worker_cpu_limit = {_toml_value(config.env.worker_cpu_limit)}")
    lines.append(f"reuse_cluster = {_toml_value(config.env.reuse_cluster)}")
    lines.append(f"force_recreate_cluster = {_toml_value(config.env.force_recreate_cluster)}")
    lines.append(f"submit_done_returns_feedback = {_toml_value(config.env.submit_done_returns_feedback)}")
    lines.append(f"cleanup_defer_timeout_seconds = {_toml_value(config.env.cleanup_defer_timeout_seconds)}")

    agent_configs = promote_crucible_legacy_config(
        agent=config.agent,
        agent_config=config.agent_config,
        enable_summary=config.enable_summary,
        no_inject_summary=config.no_inject_summary,
        crucible_seed_kb_dir=config.env.crucible_seed_kb_dir,
    )
    for agent_name, agent_cfg in agent_configs.items():
        lines.append("")
        lines.append(f"[agent.{agent_name}]")
        for k, v in agent_cfg.items():
            lines.append(f"{k} = {_toml_value(v)}")

    lines.append("")
    return "\n".join(lines) + "\n"
