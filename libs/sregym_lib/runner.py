"""SREGym experiment + pipeline runner.

Pure orchestration — resolving configs, creating experiment directories,
translating :class:`ExperimentConfig` into CLI args + env vars for
``bench/sregym/main.py``, and running pipelines with resume/rerun support.

Agent-specific concerns (for example crucible's knowledge-base seeding and
between-stage KB drain barrier) are injected via :class:`ExpStageLifecycle`.
Each agent package under ``sregym_agents/`` may expose its own lifecycle
definition; this module stays agent-agnostic.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from libs.sregym_lib.experiment import (
    ExperimentConfig,
    application_workspace_mode,
    config_to_env,
    config_to_main_args,
    read_snapshot,
    resolve_config,
    resolve_tasklist,
    write_snapshot,
)
from libs.sregym_lib.pipeline import (
    PipelineConfig,
    PipelineState,
    StageState,
    merge_stage_config,
    reconcile_pipeline_state,
    write_pipeline_snapshot,
    write_pipeline_state,
)

_APP_WORKSPACE_SEED_ENV_VAR = "SREGYM_APP_WORKSPACE_SEED_DIR"


def _load_agent_hooks(agent_name: str, project_root: Path) -> tuple[str | None, str | None]:
    """Return (before_benchmark, after_benchmark) for *agent_name*."""
    import yaml

    agents_yaml = project_root / "sregym_agents" / "agents.yaml"
    if not agents_yaml.exists():
        return None, None

    raw_data: object = yaml.safe_load(agents_yaml.read_text())
    if not isinstance(raw_data, dict):
        return None, None
    data = cast("dict[str, object]", raw_data)
    agents_raw = data.get("agents", [])
    if not isinstance(agents_raw, list):
        return None, None

    for raw_agent in cast("list[object]", agents_raw):
        if not isinstance(raw_agent, dict):
            continue
        agent = cast("dict[str, object]", raw_agent)
        if agent.get("name") == agent_name:
            before = agent.get("before_benchmark")
            after = agent.get("after_benchmark")
            return (
                before if isinstance(before, str) else None,
                after if isinstance(after, str) else None,
            )
    return None, None


def _run_hook(cmd: str, env: dict[str, str], label: str, project_root: Path) -> None:
    """Run a lifecycle hook shell command from the project root."""
    print(f"  {label}: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=str(project_root), env=env)  # noqa: S602
    if result.returncode != 0:
        print(f"  ⚠️  {label} exited with code {result.returncode}", flush=True)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@runtime_checkable
class ExpStageLifecycle(Protocol):
    """Agent-specific behavior invoked during experiment stage lifecycle."""

    def before_stage(self, exp_dir: Path, config: ExperimentConfig) -> None:
        """Run before a stage or single experiment starts."""

    def snapshot_before_drain(self, exp_dir: Path, config: ExperimentConfig) -> object | None:
        """Capture any baseline needed before post-stage drain waiting."""

    def wait_for_drain(self, exp_dir: Path, baseline: object | None) -> None:
        """Block until any agent-specific post-stage work has drained."""


class _NoopExpStageLifecycle:
    def before_stage(self, exp_dir: Path, config: ExperimentConfig) -> None:
        del exp_dir, config

    def snapshot_before_drain(self, exp_dir: Path, config: ExperimentConfig) -> object | None:
        del exp_dir, config
        return None

    def wait_for_drain(self, exp_dir: Path, baseline: object | None) -> None:
        del exp_dir, baseline


NOOP_EXP_STAGE_LIFECYCLE: ExpStageLifecycle = _NoopExpStageLifecycle()


# ---------------------------------------------------------------------------
# Single experiment
# ---------------------------------------------------------------------------


def _create_experiment_dir(config: ExperimentConfig, sregym_dir: Path) -> Path:
    logs_root = sregym_dir / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = f"{timestamp}_{config.agent}"
    exp_dir = logs_root / dir_name
    exp_dir.mkdir(parents=True, exist_ok=True)
    return exp_dir


def _print_experiment_info(config: ExperimentConfig, env: dict[str, str]) -> None:
    print(f"  agent={config.agent}  model={config.model}  parallel={config.parallel}")
    if config.variants.enabled:
        variants = config.variants
        print(f"  variants: count={variants.count}  offset={variants.offset}  seed={variants.seed}")
    elif config.tasklist:
        print(f"  tasklist={config.tasklist}")
    elif config.problems:
        print(f"  problems={config.problems}")
    agent_cfg_json = env.get("SREGYM_EXPERIMENT_AGENT_CONFIG", "")
    if agent_cfg_json:
        print(f"  agent config: {agent_cfg_json}")


def _verify_sregym(sregym_dir: Path) -> None:
    main_py = sregym_dir / "main.py"
    if not main_py.exists():
        print(
            f"Error: {main_py} not found. Is the sregym submodule checked out?",
            file=sys.stderr,
        )
        sys.exit(1)


def run_single_experiment(
    target: Path,
    extra_args: list[str],
    *,
    project_root: Path,
    sregym_dir: Path,
    lifecycle: ExpStageLifecycle | None = None,
) -> None:
    """Run or resume a single experiment."""
    lifecycle = lifecycle or NOOP_EXP_STAGE_LIFECYCLE

    if target.is_dir():
        exp_dir = target.resolve()
        config = read_snapshot(exp_dir)
        config = resolve_config(config)
        tasklist_path: Path | None = exp_dir / "tasklist.yml"
        if not tasklist_path.exists():
            tasklist_path = None
        print(f"Resuming experiment from: {exp_dir}")
    else:
        config = load_experiment_config_or_resolve(target)
        exp_dir = _create_experiment_dir(config, sregym_dir)
        write_snapshot(config, exp_dir)
        tasklist_path = resolve_tasklist(config, sregym_dir, exp_dir)
        print(f"New experiment: {exp_dir}")

    _verify_sregym(sregym_dir)

    cli_args = config_to_main_args(config, exp_dir, tasklist_path)
    cli_args.extend(extra_args)
    env = config_to_env(config, project_root)

    _print_experiment_info(config, env)
    print()

    lifecycle.before_stage(exp_dir, config)

    before_hook, after_hook = _load_agent_hooks(config.agent, project_root)
    if before_hook:
        _run_hook(before_hook, env, "before_benchmark", project_root)

    os.chdir(sregym_dir)
    argv = ["uv", "run", "main.py"] + cli_args
    print(f"  exec: {' '.join(argv)}")

    if after_hook:
        result = subprocess.run(argv, env=env)
        _run_hook(after_hook, env, "after_benchmark", project_root)
        sys.exit(result.returncode)

    os.execvpe("uv", argv, env)


def load_experiment_config_or_resolve(path: Path) -> ExperimentConfig:
    """Load a TOML experiment config and apply env-var overrides."""
    from libs.sregym_lib.experiment import load_experiment_config

    return resolve_config(load_experiment_config(path))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _create_pipeline_dir(config: PipelineConfig, sregym_dir: Path) -> Path:
    logs_root = sregym_dir / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{config.name}" if config.name else ""
    dir_name = f"{timestamp}_pipeline{suffix}"
    pipeline_dir = logs_root / dir_name
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    return pipeline_dir


def _run_stage(
    exp_config: ExperimentConfig,
    stage_exp_dir: Path,
    tasklist_path: Path | None,
    sregym_dir: Path,
    project_root: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    cli_args = config_to_main_args(exp_config, stage_exp_dir, tasklist_path)
    env = config_to_env(exp_config, project_root)
    if extra_env:
        env.update(extra_env)

    _print_experiment_info(exp_config, env)

    argv = ["uv", "run", "main.py"] + cli_args
    print(f"  exec: {' '.join(argv)}")
    print()
    result = subprocess.run(argv, cwd=str(sregym_dir), env=env)
    if result.returncode != 0:
        print(f"  ⚠️  Stage exited with code {result.returncode}", flush=True)
        if result.returncode < 0:
            import signal as _sig

            try:
                sig_name = _sig.Signals(-result.returncode).name
            except (ValueError, AttributeError):
                sig_name = f"signal {-result.returncode}"
            print(f"  ⚠️  Process was killed by {sig_name}", flush=True)
    return result.returncode


def _resolve_workspace_seed_env(
    *,
    current_stage: int,
    state: PipelineState,
    exp_config: ExperimentConfig,
) -> dict[str, str]:
    """Return env vars needed to seed a stage-local application workspace."""
    if current_stage <= 0:
        raise ValueError("chain_application_workspace requires a previous stage to copy from")
    if application_workspace_mode(exp_config.application_workspace) != "persistent":
        raise ValueError("chain_application_workspace requires application_workspace = 'persistent' for the stage")

    prev_state = state.stages[current_stage - 1]
    if not prev_state.experiment_dir:
        raise FileNotFoundError("Previous stage has no experiment directory to copy application workspace from")

    prev_stage_dir = Path(prev_state.experiment_dir)
    prev_workspace_dir = prev_stage_dir / "application_workspace"
    if not prev_workspace_dir.is_dir():
        raise FileNotFoundError(f"Previous stage application workspace is missing: {prev_workspace_dir}")

    prev_config = read_snapshot(prev_stage_dir)
    if application_workspace_mode(prev_config.application_workspace) != "persistent":
        raise ValueError(
            "chain_application_workspace requires the previous stage to enable application_workspace = 'persistent'"
        )
    if prev_config.app_filter != exp_config.app_filter:
        raise ValueError(
            "chain_application_workspace requires matching app_filter values between consecutive stages "
            f"(previous={prev_config.app_filter!r}, current={exp_config.app_filter!r})"
        )

    return {_APP_WORKSPACE_SEED_ENV_VAR: str(prev_workspace_dir)}


def run_pipeline(
    config: PipelineConfig,
    *,
    project_root: Path,
    sregym_dir: Path,
    pipeline_dir: Path | None = None,
    state: PipelineState | None = None,
    lifecycle: ExpStageLifecycle | None = None,
) -> int:
    """Run a multi-stage pipeline with automatic KB chaining."""
    lifecycle = lifecycle or NOOP_EXP_STAGE_LIFECYCLE

    _verify_sregym(sregym_dir)

    if pipeline_dir is None:
        pipeline_dir = _create_pipeline_dir(config, sregym_dir)
        write_pipeline_snapshot(config, pipeline_dir)
        state = PipelineState(
            stages=[StageState(index=i, name=s.name, status="pending") for i, s in enumerate(config.stages)]
        )
        write_pipeline_state(state, pipeline_dir)
        print(f"New pipeline: {pipeline_dir}")
    else:
        print(f"Resuming pipeline from: {pipeline_dir}")

    assert state is not None
    reconcile_pipeline_state(config, state)
    write_pipeline_state(state, pipeline_dir)

    print(f"  stages: {len(config.stages)}")
    print()

    before_hook = after_hook = None
    hook_env: dict[str, str] = dict(os.environ)
    if config.stages:
        hook_exp = merge_stage_config(config.defaults, {})
        hook_exp = resolve_config(hook_exp)
        hook_env = config_to_env(hook_exp, project_root)
        before_hook, after_hook = _load_agent_hooks(hook_exp.agent, project_root)

    if before_hook:
        _run_hook(before_hook, hook_env, "before_benchmark", project_root)

    prev_kb_dir: str | None = None

    try:
        for i, stage_cfg in enumerate(config.stages):
            stage_state = state.stages[i]

            if stage_state.status == "completed":
                if stage_state.experiment_dir:
                    prev_kb_dir = str(Path(stage_state.experiment_dir) / "kb")
                print(
                    f"Stage {i}/{len(config.stages) - 1}: "
                    f"{stage_cfg.name or f'stage_{i}'} [skipped — already completed]"
                )
                continue

            exp_config = merge_stage_config(config.defaults, stage_cfg.runner_overrides)
            exp_config = resolve_config(exp_config)

            if stage_cfg.chain_kb and prev_kb_dir:
                exp_config = dataclasses.replace(
                    exp_config,
                    env=dataclasses.replace(
                        exp_config.env,
                        crucible_seed_kb_dir=prev_kb_dir,
                    ),
                )

            stage_name = stage_cfg.name or f"stage_{i}"
            stage_exp_dir = pipeline_dir / f"stage_{i}_{stage_name}"
            stage_exp_dir.mkdir(parents=True, exist_ok=True)

            write_snapshot(exp_config, stage_exp_dir)
            tasklist_path = resolve_tasklist(exp_config, sregym_dir, stage_exp_dir)

            stage_state.status = "running"
            stage_state.experiment_dir = str(stage_exp_dir)
            write_pipeline_state(state, pipeline_dir)

            print("=" * 60)
            print(f"Stage {i}/{len(config.stages) - 1}: {stage_name}")
            if stage_cfg.chain_kb and prev_kb_dir:
                print(f"  KB seed: {prev_kb_dir}")
            stage_extra_env: dict[str, str] = {}
            try:
                if stage_cfg.chain_application_workspace:
                    stage_extra_env = _resolve_workspace_seed_env(
                        current_stage=i,
                        state=state,
                        exp_config=exp_config,
                    )
                    print(f"  Application workspace seed: {stage_extra_env[_APP_WORKSPACE_SEED_ENV_VAR]}")
            except Exception as exc:
                stage_state.status = "failed"
                stage_state.error = str(exc)
                write_pipeline_state(state, pipeline_dir)
                print(f"\nStage {i} failed before launch: {exc}")
                print(f"Resume with: run_sregym.sh {pipeline_dir}")
                return 1
            print("=" * 60)

            lifecycle.before_stage(stage_exp_dir, exp_config)

            needs_kb_barrier = i + 1 < len(config.stages) and config.stages[i + 1].chain_kb
            drain_baseline: object | None = None
            if needs_kb_barrier:
                drain_baseline = lifecycle.snapshot_before_drain(stage_exp_dir, exp_config)

            try:
                returncode = _run_stage(
                    exp_config,
                    stage_exp_dir,
                    tasklist_path,
                    sregym_dir,
                    project_root,
                    stage_extra_env,
                )
            except KeyboardInterrupt:
                print(f"\nInterrupted during stage {i}. Saving state for resume.")
                stage_state.status = "failed"
                stage_state.error = "interrupted"
                write_pipeline_state(state, pipeline_dir)
                print(f"Resume with: run_sregym.sh {pipeline_dir}")
                return 1

            if returncode != 0:
                stage_state.status = "failed"
                stage_state.error = f"exit code {returncode}"
                write_pipeline_state(state, pipeline_dir)
                print(f"\nStage {i} failed (exit code {returncode}). Pipeline aborted.")
                print(f"Resume with: run_sregym.sh {pipeline_dir}")
                return 1

            if needs_kb_barrier:
                print("  Waiting for KB review queue to drain before chaining...")
                try:
                    lifecycle.wait_for_drain(stage_exp_dir, drain_baseline)
                except KeyboardInterrupt:
                    print(f"\nInterrupted while waiting for KB queue after stage {i}. Saving state for resume.")
                    stage_state.status = "failed"
                    stage_state.error = "interrupted while waiting for kb queue"
                    write_pipeline_state(state, pipeline_dir)
                    print(f"Resume with: run_sregym.sh {pipeline_dir}")
                    return 1
                except Exception as exc:
                    stage_state.status = "failed"
                    stage_state.error = f"kb queue drain failed: {exc}"
                    write_pipeline_state(state, pipeline_dir)
                    print(f"\nStage {i} failed while waiting for KB queue: {exc}")
                    print(f"Resume with: run_sregym.sh {pipeline_dir}")
                    return 1

            stage_state.status = "completed"
            write_pipeline_state(state, pipeline_dir)
            prev_kb_dir = str(stage_exp_dir / "kb")
            print(f"\nStage {i} completed.\n")

        print("=" * 60)
        print("Pipeline completed successfully.")
        print(f"  directory: {pipeline_dir}")
        print("=" * 60)
        return 0
    finally:
        if after_hook:
            _run_hook(after_hook, hook_env, "after_benchmark", project_root)
