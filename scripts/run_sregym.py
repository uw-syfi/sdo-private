#!/usr/bin/env python3
"""SREGym experiment launcher.

Supports both single experiments and multi-stage pipelines with
automatic knowledge base chaining.

Usage:
    # New single experiment:
    uv run python scripts/run_sregym.py sregym_agents/experiments/default.toml

    # New pipeline (auto-detected by [[stages]] in TOML):
    uv run python scripts/run_sregym.py sregym_agents/experiments/example_pipeline.toml

    # Resume experiment or pipeline:
    uv run python scripts/run_sregym.py bench/sregym/logs/<exp_or_pipeline_dir>/

    # Rerun a specific pipeline stage:
    uv run python scripts/run_sregym.py bench/sregym/logs/<pipeline_dir>/ --stage 1
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path so sregym_agents is importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from sregym_agents.crucible.kb_update_queue import (  # noqa: E402
    snapshot_kb_queue,
    wait_for_kb_queue_drain,
)
from sregym_agents.crucible.knowledge_base import seed_kb  # noqa: E402
from sregym_agents.experiment_config import (  # noqa: E402
    ExperimentConfig,
    config_to_env,
    config_to_main_args,
    load_experiment_config,
    read_snapshot,
    resolve_config,
    resolve_tasklist,
    write_snapshot,
)
from sregym_agents.pipeline_config import (  # noqa: E402
    PipelineConfig,
    PipelineState,
    StageState,
    has_pipeline_state,
    is_pipeline_config,
    load_pipeline_config,
    merge_stage_config,
    read_pipeline_snapshot,
    read_pipeline_state,
    reset_stages_for_rerun,
    write_pipeline_snapshot,
    write_pipeline_state,
)

_SREGYM_DIR = _PROJECT_ROOT / "bench" / "sregym"
_KB_QUEUE_DRAIN_TIMEOUT_S = 1800.0
_KB_QUEUE_DRAIN_POLL_INTERVAL_S = 2.0
_AGENTS_YAML = _PROJECT_ROOT / "sregym_agents" / "agents.yaml"


def _load_agent_hooks(agent_name: str) -> tuple[str | None, str | None]:
    """Return (before_benchmark, after_benchmark) shell commands for *agent_name*, or (None, None)."""
    import yaml

    if not _AGENTS_YAML.exists():
        return None, None
    data = yaml.safe_load(_AGENTS_YAML.read_text())
    for agent in data.get("agents", []):
        if agent.get("name") == agent_name:
            return agent.get("before_benchmark"), agent.get("after_benchmark")
    return None, None


def _run_hook(cmd: str, env: dict[str, str], label: str) -> None:
    """Run a lifecycle hook shell command from the project root. Warns on failure."""
    print(f"  {label}: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=str(_PROJECT_ROOT), env=env)  # noqa: S602
    if result.returncode != 0:
        print(f"  ⚠️  {label} exited with code {result.returncode}", flush=True)


_MEMORY_AGENT = "cli_agent"
_DEFAULT_MEMORY_PORT = 9953


def _inject_memory_defaults(env: dict[str, str], agent_name: str, log_dir: Path) -> dict[str, str]:
    """Inject default memory_store / memory_port into SREGYM_EXPERIMENT_AGENT_CONFIG.

    Only applied for *agent_name* == ``cli_agent`` and only when neither key
    is already present (explicit config is never overridden).
    """
    if agent_name != _MEMORY_AGENT:
        return env
    raw = env.get("SREGYM_EXPERIMENT_AGENT_CONFIG", "{}")
    try:
        cfg: dict = json.loads(raw)
    except json.JSONDecodeError:
        cfg = {}
    if not isinstance(cfg, dict):
        return env
    if "memory_store" in cfg or "memory_port" in cfg:
        return env  # user has explicit config
    env = dict(env)
    cfg["memory_store"] = str(log_dir / "kb" / "incidents.db")
    cfg["memory_port"] = _DEFAULT_MEMORY_PORT
    env["SREGYM_EXPERIMENT_AGENT_CONFIG"] = json.dumps(cfg)
    return env


# ---------------------------------------------------------------------------
# Single experiment (unchanged logic, extracted to function)
# ---------------------------------------------------------------------------


def _create_experiment_dir(config: ExperimentConfig) -> Path:
    """Create a new experiment directory under bench/sregym/logs/."""
    logs_root = _SREGYM_DIR / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = f"{timestamp}_{config.agent}"
    exp_dir = logs_root / dir_name
    exp_dir.mkdir(parents=True, exist_ok=True)
    return exp_dir


def _print_experiment_info(config: ExperimentConfig, env: dict[str, str]) -> None:
    print(f"  agent={config.agent}  model={config.model}  parallel={config.parallel}")
    if config.variants.enabled:
        v = config.variants
        print(f"  variants: count={v.count}  offset={v.offset}  seed={v.seed}")
    elif config.tasklist:
        print(f"  tasklist={config.tasklist}")
    elif config.problems:
        print(f"  problems={config.problems}")
    agent_cfg_json = env.get("SREGYM_EXPERIMENT_AGENT_CONFIG", "")
    if agent_cfg_json:
        print(f"  agent config: {agent_cfg_json}")


def _verify_sregym() -> None:
    main_py = _SREGYM_DIR / "main.py"
    if not main_py.exists():
        print(
            "Error: bench/sregym/main.py not found. Is the sregym submodule checked out?",
            file=sys.stderr,
        )
        sys.exit(1)


def run_single_experiment(target: Path, extra_args: list[str]) -> None:
    """Run or resume a single experiment (original behavior, uses execvpe)."""
    if target.is_dir():
        exp_dir = target.resolve()
        config = read_snapshot(exp_dir)
        config = resolve_config(config)
        tasklist_path = exp_dir / "tasklist.yml"
        if not tasklist_path.exists():
            tasklist_path = None
        print(f"Resuming experiment from: {exp_dir}")
    else:
        config = load_experiment_config(target)
        config = resolve_config(config)
        exp_dir = _create_experiment_dir(config)
        write_snapshot(config, exp_dir)
        tasklist_path = resolve_tasklist(config, _SREGYM_DIR, exp_dir)
        print(f"New experiment: {exp_dir}")

    _verify_sregym()

    cli_args = config_to_main_args(config, exp_dir, tasklist_path)
    cli_args.extend(extra_args)
    env = config_to_env(config, _PROJECT_ROOT)

    _print_experiment_info(config, env)
    print()

    seed_kb(exp_dir / "kb", config.env.crucible_seed_kb_dir or None)

    env = _inject_memory_defaults(env, config.agent, exp_dir)
    before_hook, after_hook = _load_agent_hooks(config.agent)
    if before_hook:
        _run_hook(before_hook, env, "before_benchmark")

    os.chdir(_SREGYM_DIR)
    argv = ["uv", "run", "main.py"] + cli_args
    print(f"  exec: {' '.join(argv)}")

    if after_hook:
        # Keep this process alive so the after_benchmark hook can run on exit.
        result = subprocess.run(argv, env=env)
        _run_hook(after_hook, env, "after_benchmark")
        sys.exit(result.returncode)
    else:
        os.execvpe("uv", argv, env)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _create_pipeline_dir(config: PipelineConfig) -> Path:
    """Create a new pipeline directory under bench/sregym/logs/."""
    logs_root = _SREGYM_DIR / "logs"
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
    memory_log_dir: Path | None = None,
) -> int:
    """Run a single pipeline stage via subprocess. Returns exit code."""
    cli_args = config_to_main_args(exp_config, stage_exp_dir, tasklist_path)
    env = config_to_env(exp_config, _PROJECT_ROOT)
    if memory_log_dir is not None:
        env = _inject_memory_defaults(env, exp_config.agent, memory_log_dir)

    _print_experiment_info(exp_config, env)

    argv = ["uv", "run", "main.py"] + cli_args
    print(f"  exec: {' '.join(argv)}")
    print()
    result = subprocess.run(argv, cwd=str(_SREGYM_DIR), env=env)
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


def run_pipeline(
    config: PipelineConfig,
    pipeline_dir: Path | None = None,
    state: PipelineState | None = None,
) -> int:
    """Run a multi-stage pipeline with automatic KB chaining.

    If *pipeline_dir* and *state* are provided, resumes from existing
    state (skipping completed stages).  Otherwise creates a new
    pipeline directory.

    Returns 0 on success, 1 on failure.
    """
    _verify_sregym()

    # New pipeline
    if pipeline_dir is None:
        pipeline_dir = _create_pipeline_dir(config)
        write_pipeline_snapshot(config, pipeline_dir)
        state = PipelineState(
            stages=[StageState(index=i, name=s.name, status="pending") for i, s in enumerate(config.stages)]
        )
        write_pipeline_state(state, pipeline_dir)
        print(f"New pipeline: {pipeline_dir}")
    else:
        print(f"Resuming pipeline from: {pipeline_dir}")

    assert state is not None
    print(f"  stages: {len(config.stages)}")
    print()

    # Build hook env from defaults so hooks run once for the whole pipeline.
    before_hook = after_hook = None
    hook_env: dict[str, str] = dict(os.environ)
    if config.stages:
        hook_exp = merge_stage_config(config.defaults, {})
        hook_exp = resolve_config(hook_exp)
        hook_env = config_to_env(hook_exp, _PROJECT_ROOT)
        hook_env = _inject_memory_defaults(hook_env, hook_exp.agent, pipeline_dir)
        before_hook, after_hook = _load_agent_hooks(hook_exp.agent)

    if before_hook:
        _run_hook(before_hook, hook_env, "before_benchmark")

    prev_kb_dir: str | None = None
    exit_code = 0

    try:
        for i, stage_cfg in enumerate(config.stages):
            stage_state = state.stages[i]

            # Skip completed stages, but track their KB dir for chaining
            if stage_state.status == "completed":
                if stage_state.experiment_dir:
                    prev_kb_dir = str(Path(stage_state.experiment_dir) / "kb")
                stage_label = stage_cfg.name or f"stage_{i}"
                print(f"Stage {i}/{len(config.stages) - 1}: {stage_label} [skipped — already completed]")
                continue

            # Build ExperimentConfig for this stage
            exp_config = merge_stage_config(config.defaults, stage_cfg.runner_overrides)
            exp_config = resolve_config(exp_config)

            # KB chaining: set seed dir from previous completed stage
            if stage_cfg.chain_kb and prev_kb_dir:
                exp_config = dataclasses.replace(
                    exp_config,
                    env=dataclasses.replace(
                        exp_config.env,
                        crucible_seed_kb_dir=prev_kb_dir,
                    ),
                )

            # Create stage experiment dir
            stage_name = stage_cfg.name or f"stage_{i}"
            stage_exp_dir = pipeline_dir / f"stage_{i}_{stage_name}"
            stage_exp_dir.mkdir(parents=True, exist_ok=True)

            write_snapshot(exp_config, stage_exp_dir)
            tasklist_path = resolve_tasklist(exp_config, _SREGYM_DIR, stage_exp_dir)

            # Update state
            stage_state.status = "running"
            stage_state.experiment_dir = str(stage_exp_dir)
            write_pipeline_state(state, pipeline_dir)

            # Print stage header
            print("=" * 60)
            print(f"Stage {i}/{len(config.stages) - 1}: {stage_name}")
            if stage_cfg.chain_kb and prev_kb_dir:
                print(f"  KB seed: {prev_kb_dir}")
            print("=" * 60)

            seed_kb(stage_exp_dir / "kb", exp_config.env.crucible_seed_kb_dir or None)
            needs_kb_barrier = i + 1 < len(config.stages) and config.stages[i + 1].chain_kb
            kb_queue_baseline = snapshot_kb_queue(stage_exp_dir / "kb") if needs_kb_barrier else None

            try:
                returncode = _run_stage(exp_config, stage_exp_dir, tasklist_path, memory_log_dir=pipeline_dir)
            except KeyboardInterrupt:
                print(f"\nInterrupted during stage {i}. Saving state for resume.")
                stage_state.status = "failed"
                stage_state.error = "interrupted"
                write_pipeline_state(state, pipeline_dir)
                print(f"Resume with: run_sregym.sh {pipeline_dir}")
                exit_code = 1
                break

            if returncode != 0:
                stage_state.status = "failed"
                stage_state.error = f"exit code {returncode}"
                write_pipeline_state(state, pipeline_dir)
                print(f"\nStage {i} failed (exit code {returncode}). Pipeline aborted.")
                print(f"Resume with: run_sregym.sh {pipeline_dir}")
                exit_code = 1
                break

            if needs_kb_barrier:
                print("  Waiting for KB review queue to drain before chaining...")
                try:
                    wait_for_kb_queue_drain(
                        stage_exp_dir / "kb",
                        baseline=kb_queue_baseline,
                        timeout_s=_KB_QUEUE_DRAIN_TIMEOUT_S,
                        poll_interval_s=_KB_QUEUE_DRAIN_POLL_INTERVAL_S,
                    )
                except KeyboardInterrupt:
                    print(f"\nInterrupted while waiting for KB queue after stage {i}. Saving state for resume.")
                    stage_state.status = "failed"
                    stage_state.error = "interrupted while waiting for kb queue"
                    write_pipeline_state(state, pipeline_dir)
                    print(f"Resume with: run_sregym.sh {pipeline_dir}")
                    exit_code = 1
                    break
                except Exception as exc:
                    stage_state.status = "failed"
                    stage_state.error = f"kb queue drain failed: {exc}"
                    write_pipeline_state(state, pipeline_dir)
                    print(f"\nStage {i} failed while waiting for KB queue: {exc}")
                    print(f"Resume with: run_sregym.sh {pipeline_dir}")
                    exit_code = 1
                    break

            # Mark completed
            stage_state.status = "completed"
            write_pipeline_state(state, pipeline_dir)
            prev_kb_dir = str(stage_exp_dir / "kb")
            print(f"\nStage {i} completed.\n")

        if exit_code == 0:
            print("=" * 60)
            print("Pipeline completed successfully.")
            print(f"  directory: {pipeline_dir}")
            print("=" * 60)
    finally:
        if after_hook:
            _run_hook(after_hook, hook_env, "after_benchmark")

    return exit_code


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _parse_stage_arg(args: list[str]) -> tuple[int | None, list[str]]:
    """Extract --stage N from args. Returns (stage_index, remaining_args)."""
    remaining = []
    stage_index = None
    i = 0
    while i < len(args):
        if args[i] == "--stage" and i + 1 < len(args):
            stage_index = int(args[i + 1])
            i += 2
        else:
            remaining.append(args[i])
            i += 1
    return stage_index, remaining


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: run_sregym.py <config.toml | experiment_dir> [--stage N]",
            file=sys.stderr,
        )
        sys.exit(1)

    target = Path(sys.argv[1])
    extra_args = sys.argv[2:]
    stage_index, extra_args = _parse_stage_arg(extra_args)

    # --- Directory target: resume pipeline or single experiment ---
    if target.is_dir():
        target = target.resolve()
        if has_pipeline_state(target):
            config = read_pipeline_snapshot(target)
            state = read_pipeline_state(target)
            if stage_index is not None:
                reset_stages_for_rerun(config, state, stage_index, target)
                write_pipeline_state(state, target)
                print(f"Resetting from stage {stage_index} for rerun.")
            sys.exit(run_pipeline(config, pipeline_dir=target, state=state))
        else:
            if stage_index is not None:
                print("Error: --stage is only supported for pipeline directories.", file=sys.stderr)
                sys.exit(1)
            run_single_experiment(target, extra_args)

    # --- TOML file target: new pipeline or single experiment ---
    elif target.is_file() and target.suffix == ".toml":
        if stage_index is not None:
            print("Error: --stage is only supported when resuming a pipeline directory.", file=sys.stderr)
            sys.exit(1)
        if is_pipeline_config(target):
            config = load_pipeline_config(target)
            sys.exit(run_pipeline(config))
        else:
            run_single_experiment(target, extra_args)

    else:
        print(
            f"Error: '{target}' is neither a .toml config file nor an existing experiment directory.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
